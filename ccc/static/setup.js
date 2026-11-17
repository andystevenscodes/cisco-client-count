"use strict";

(function () {
  const $ = (id) => document.getElementById(id);
  const root = $("setup");
  const RECEIVER = "CLIENT_COUNT";
  const CLIENT_XPATH = "/wireless-client-oper:client-oper-data/common-oper-data";
  const AP_XPATH = "/wireless-access-point-oper:access-point-oper-data/capwap-data";
  const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;
  const isIp = (value) => IPV4.test(value) || (value.includes(":") && /^[0-9a-fA-F:.]+$/.test(value));

  // Prefill from the address this page was opened on, when that is an IP.
  if (isIp(root.dataset.host) && !["127.0.0.1", "::1"].includes(root.dataset.host)) {
    $("receiver").value = root.dataset.host;
  }
  $("port").value = root.dataset.port;
  for (const id of ["receiver", "source", "style", "client-id", "ap-id", "client-period", "ap-period"]) {
    const saved = sessionStorage.getItem("setup-" + id);
    if (saved !== null && saved !== "") $(id).value = saved;
  }

  function subscription(id, xpath, seconds, receiver, port, source, style) {
    const lines = [
      `telemetry ietf subscription ${id}`,
      ` encoding encode-kvgpb`,
      ` filter xpath ${xpath}`,
    ];
    if (source) lines.push(` source-address ${source}`);
    if (style === "named") lines.push(` receiver-type protocol`);
    lines.push(` stream yang-push`, ` update-policy periodic ${seconds * 100}`);
    lines.push(style === "named"
      ? ` receiver name ${RECEIVER}`
      : ` receiver ip address ${receiver} ${port} protocol grpc-tcp`);
    return lines;
  }

  function render() {
    const receiver = $("receiver").value.trim();
    const source = $("source").value.trim();
    const port = $("port").value.trim();
    const style = $("style").value;
    const clientId = $("client-id").value.trim();
    const apId = $("ap-id").value.trim();
    const clientPeriod = Number($("client-period").value);
    const apPeriod = Number($("ap-period").value);
    for (const id of ["receiver", "source", "style", "client-id", "ap-id", "client-period", "ap-period"]) {
      sessionStorage.setItem("setup-" + id, $(id).value);
    }

    const problems = [];
    if (!receiver) problems.push("Fill in the address of this server.");
    else if (!isIp(receiver)) problems.push("The address of this server must be an IP address.");
    if (source && !isIp(source)) problems.push("The source address must be an IP address.");
    if (!/^\d+$/.test(clientId) || (apPeriod && !/^\d+$/.test(apId))) problems.push("A subscription id is a number.");
    if (apPeriod && clientId === apId) problems.push("The two subscriptions need different ids.");
    $("problem").hidden = problems.length === 0;
    $("problem").textContent = problems.join(" ");

    const shownReceiver = receiver || "<this-server-ip>";
    const config = [
      "! Prerequisite: telemetry needs the YANG interface (NETCONF) on the controller.",
      "! Check with:  show netconf-yang status",
      "! Only when it is not running, enable it first with:  netconf-yang",
      "!",
    ];
    if (style === "named") {
      config.push(`telemetry receiver protocol ${RECEIVER}`, ` host ip-address ${shownReceiver} ${port}`, ` protocol grpc-tcp`, "!");
    }
    config.push(`! Clients, one row per client, every ${clientPeriod} s`);
    config.push(...subscription(clientId, CLIENT_XPATH, clientPeriod, shownReceiver, port, source, style), "!");
    if (apPeriod) {
      config.push(`! Access points, one row per access point, every ${apPeriod} s`);
      config.push(...subscription(apId, AP_XPATH, apPeriod, shownReceiver, port, source, style), "!");
    }
    config.push("end");
    $("config").textContent = "configure terminal\n" + config.join("\n");

    const ids = apPeriod ? [clientId, apId] : [clientId];
    $("verify").textContent = [
      ...ids.map((id) => `show telemetry ietf subscription ${id} detail`),
      ...ids.map((id) => `show telemetry ietf subscription ${id} receiver`),
      "show telemetry connection all",
    ].join("\n");
    $("remove").textContent = ["configure terminal",
      ...ids.map((id) => `no telemetry ietf subscription ${id}`),
      ...(style === "named" ? [`no telemetry receiver protocol ${RECEIVER}`] : []),
      "end"].join("\n");

    const perDay = Math.round(86400 / clientPeriod);
    $("volume").textContent = `Stores ${perDay.toLocaleString("en-GB")} rows per access point per day.`;
  }

  root.addEventListener("input", render);
  for (const button of document.querySelectorAll("button[data-copy]")) {
    button.addEventListener("click", () => ccc.copy($(button.dataset.copy).textContent, button));
  }
  render();
})();
