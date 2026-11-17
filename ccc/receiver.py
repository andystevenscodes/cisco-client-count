"""gRPC dial-out receiver.

The controller opens the connection (dial-out) and streams MdtDialoutArgs
messages; `data` in each one is a serialized Telemetry message. Nothing is
sent back.
"""

import logging
from concurrent import futures

import grpc

from ccc.pb import mdt_dialout_pb2

log = logging.getLogger("ccc.receiver")

SERVICE = "mdt_dialout.gRPCMdtDialout"


def peer_ip(peer):
    """'ipv4:10.0.0.5:51234' / 'ipv6:[2001:db8::1]:51234' -> the address."""
    if peer.startswith("ipv4:"):
        return peer[5:].rsplit(":", 1)[0]
    if peer.startswith("ipv6:"):
        rest = peer[5:].rsplit(":", 1)[0].strip("[]")
        # gRPC percent-encodes the brackets
        rest = rest.replace("%5B", "").replace("%5D", "")
        if rest.lower().startswith("::ffff:") and "." in rest:
            return rest[7:]
        return rest
    return peer


class Receiver:
    def __init__(self, config, pipeline):
        self.config = config
        self.pipeline = pipeline
        self.server = None

    def _dialout(self, request_iterator, context):
        ip = peer_ip(context.peer())
        if not self.config.source_allowed(ip):
            self.pipeline.note_error(ip, "connection refused: source not in ALLOWED_WLC_SOURCES")
            context.abort(grpc.StatusCode.PERMISSION_DENIED, "source not allowed")
        peer = context.peer()
        self.pipeline.connection_opened(peer)
        log.info("telemetry connection from %s", ip)
        try:
            pending, expected = b"", 0
            for args in request_iterator:
                if args.errors:
                    self.pipeline.note_error(ip, f"sender reported: {args.errors}")
                data = args.data
                if not data:
                    continue
                if args.totalSize > 0:
                    # A message split over several frames: collect until whole.
                    pending += data
                    expected = args.totalSize
                    if len(pending) < expected:
                        continue
                    data, pending, expected = pending, b"", 0
                self.pipeline.ingest(ip, data)
        except grpc.RpcError:
            pass
        finally:
            self.pipeline.connection_closed(peer)
            log.info("telemetry connection from %s closed", ip)
        return
        yield  # a stream handler is a generator; this one never answers

    def start(self):
        handler = grpc.method_handlers_generic_handler(
            SERVICE,
            {
                "MdtDialout": grpc.stream_stream_rpc_method_handler(
                    self._dialout,
                    request_deserializer=mdt_dialout_pb2.MdtDialoutArgs.FromString,
                    response_serializer=mdt_dialout_pb2.MdtDialoutArgs.SerializeToString,
                )
            },
        )
        self.server = grpc.server(
            futures.ThreadPoolExecutor(max_workers=64, thread_name_prefix="mdt"),
            options=[
                ("grpc.max_receive_message_length", -1),
                ("grpc.keepalive_time_ms", 60_000),
                ("grpc.keepalive_permit_without_calls", 1),
            ],
        )
        self.server.add_generic_rpc_handlers((handler,))
        bound = self.server.add_insecure_port(f"[::]:{self.config.telemetry_port}")
        if not bound:
            raise RuntimeError(f"could not listen on port {self.config.telemetry_port}")
        self.server.start()
        log.info("listening for telemetry (gRPC dial-out) on port %s", self.config.telemetry_port)

    def stop(self):
        if self.server is not None:
            self.server.stop(grace=2)
