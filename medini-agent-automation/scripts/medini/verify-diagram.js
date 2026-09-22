// $EXPERIMENTAL$
// verify-diagram.js — P1.5 实机校验（headless）。
//
// 目的：证明生成的 .fta_diagram 不只是"看起来像 XML"，而是
//   1) 能被 GMF notation 元模型加载（Diagram 实例化成功）
//   2) 结构计数与 .fta 模型一致（children = eventNodes+gates，edges = connections）
//   3) 每个 <element href="x.fta#id"> 都能 resolve 到 .fta 里的真实元素（非 proxy）
//   4) 顶层 element 指向 FTAModel
// 这四点成立，GUI 的 GMF 编辑器才有东西可画。
//
// 占位符：__CASE__ __DIAGRAM_PATH__ __FTA_PATH__ __OUT_JSON__ __RUN_LOG__
//         __EXPECT_NODES__ __EXPECT_EDGES__
var CASE = "__CASE__";
var DIAGRAM_PATH = "__DIAGRAM_PATH__";
var FTA_PATH = "__FTA_PATH__";
var OUT_JSON = "__OUT_JSON__";
var RUN_LOG = "__RUN_LOG__";
var EXPECT_NODES = __EXPECT_NODES__;
var EXPECT_EDGES = __EXPECT_EDGES__;

var out = new java.io.PrintWriter(new java.io.FileWriter(RUN_LOG));
function log(s) { out.println(s); out.flush(); }
function sn(v) { return v == null ? "" : ("" + v); }
function jstr(s) { return JSON.stringify("" + s); }

// EMF Impl.toString() 前缀取 eClass 名；proxy 会带 eProxyURI 字样
function etype(obj) {
    try { var m = ("" + obj).match(/impl\.(\w+?)Impl@/); return m ? m[1] : "?"; }
    catch (e) { return "?"; }
}
function isProxy(obj) {
    try { return ("" + obj).indexOf("eProxyURI") >= 0; }
    catch (e) { return true; }
}

var fields = [];
function kv(k, v) { fields.push(" " + jstr(k) + ": " + v); }
function kvs(k, v) { fields.push(" " + jstr(k) + ": " + jstr(v)); }

try {
    log("== P1.5 verify diagram case=" + CASE + " ==");
    var EmfURI = bind("org.eclipse.emf.common", "org.eclipse.emf.common.util.URI", false);
    var ResSetClass = bind("org.eclipse.emf.ecore", "org.eclipse.emf.ecore.resource.impl.ResourceSetImpl", false);

    // ---- 0. notation 元模型可用性 ----
    var notationOk = true, notationErr = "";
    try {
        bind("org.eclipse.gmf.runtime.notation",
             "org.eclipse.gmf.runtime.notation.impl.DiagramImpl", false);
    } catch (e) { notationOk = false; notationErr = "" + e; }
    log("notation_metamodel_bind=" + notationOk + (notationErr ? " err=" + notationErr : ""));

    if (!notationOk) {
        kv("status", jstr("blocked"));
        kvs("case", CASE);
        kvs("error", "notation 元模型未注册（headless 无法校验图文件）: " + notationErr);
        var bw = new java.io.PrintWriter(new java.io.OutputStreamWriter(
            new java.io.FileOutputStream(OUT_JSON), "UTF-8"));
        bw.print("{\n" + fields.join(",\n") + "\n}\n");
        bw.close(); out.close();
        java.lang.System.exit(1);
    }

    var rs = new ResSetClass();

    // ---- 1. 加载 .fta_diagram ----
    var dRes = rs.getResource(EmfURI.createFileURI(DIAGRAM_PATH), true);
    var diag = dRes.getContents().get(0);
    var dType = etype(diag);
    log("diagram resource=" + ("" + dRes));
    log("diagram etype=" + dType + " contents=" + dRes.getContents().size());
    log("diagram head: " + sn(diag).substring(0, 500));

    var nChildren = diag.children.size();
    var nEdges = diag.edges.size();
    log("diagram children=" + nChildren + " edges=" + nEdges
        + " type=" + sn(diag.type) + " name=" + sn(diag.name));

    // ---- 2. 顶层 element → FTAModel ----
    var topEl = diag.element;
    var topType = etype(topEl);
    var topProxy = isProxy(topEl);
    log("diagram.element etype=" + topType + " proxy=" + topProxy);
    log("diagram.element toString=" + sn(topEl).substring(0, 300));

    // ---- 3. 逐 shape / edge 检查 element resolve ----
    var chResolved = 0, chProxy = 0, chTypes = {};
    for (var i = 0; i < nChildren; i++) {
        var sh = diag.children.get(i);
        var el = sh.element;
        var t = etype(el);
        if (isProxy(el)) { chProxy++; } else { chResolved++; }
        chTypes[t] = (chTypes[t] || 0) + 1;
        if (i < 3) { log("  shape[" + i + "] kind=" + t + " proxy=" + isProxy(el) + " :: " + sn(el).substring(0, 180)); }
    }
    log("children resolved=" + chResolved + " proxy=" + chProxy + " types=" + JSON.stringify(chTypes));

    var edResolved = 0, edProxy = 0, edTypes = {};
    for (var j = 0; j < nEdges; j++) {
        var eg = diag.edges.get(j);
        var eel = eg.element;
        var et = etype(eel);
        if (isProxy(eel)) { edProxy++; } else { edResolved++; }
        edTypes[et] = (edTypes[et] || 0) + 1;
        if (j < 2) { log("  edge[" + j + "] kind=" + et + " proxy=" + isProxy(eel) + " :: " + sn(eel).substring(0, 160)); }
    }
    log("edges resolved=" + edResolved + " proxy=" + edProxy + " types=" + JSON.stringify(edTypes));

    // ---- 4. 引用 .fta 资源是否随之载入 ----
    var fRes = rs.getResource(EmfURI.createFileURI(FTA_PATH), true);
    var ftaModel = fRes.getContents().get(0);
    log("fta etype=" + etype(ftaModel) + " events=" + ftaModel.events.size()
        + " gates=" + ftaModel.gates.size() + " nodes=" + ftaModel.eventNodes.size());

    // ---- 5. 判定 ----
    var checks = {
        "diagram_loaded": (dType === "Diagram"),
        "diagram_type_ok": (sn(diag.type) === "FaultTreeAnalysis"),
        "children_count_match": (nChildren === EXPECT_NODES),
        "edges_count_match": (nEdges === EXPECT_EDGES),
        "top_element_is_ftamodel": (topType === "FTAModel" && !topProxy),
        "all_children_resolved": (chProxy === 0 && chResolved === nChildren),
        "all_edges_resolved": (edProxy === 0 && edResolved === nEdges)
    };
    var failed = [];
    for (var k in checks) { if (!checks[k]) failed.push(k); }
    var verdict = failed.length === 0 ? "pass" : "fail";
    log("checks=" + JSON.stringify(checks) + " verdict=" + verdict);

    kv("status", jstr(verdict === "pass" ? "ok" : "mismatch"));
    kvs("case", CASE);
    kvs("verdict", verdict);
    kvs("failed_checks", failed.join(","));
    kv("checks", JSON.stringify(checks));
    kvs("diagram_etype", dType);
    kvs("diagram_type", sn(diag.type));
    kvs("diagram_name", sn(diag.name));
    kv("children_total", nChildren);
    kv("children_resolved", chResolved);
    kv("children_proxy", chProxy);
    kv("edges_total", nEdges);
    kv("edges_resolved", edResolved);
    kv("edges_proxy", edProxy);
    kv("expect_nodes", EXPECT_NODES);
    kv("expect_edges", EXPECT_EDGES);
    kvs("child_types", JSON.stringify(chTypes));
    kvs("edge_types", JSON.stringify(edTypes));
    kvs("top_element_type", topType);
    kv("top_element_proxy", topProxy ? "true" : "false");
    kv("fta_events", ftaModel.events.size());
    kv("fta_gates", ftaModel.gates.size());
    kv("fta_eventNodes", ftaModel.eventNodes.size());
    kv("fta_connections", ftaModel.connections.size());

    var jw = new java.io.PrintWriter(new java.io.OutputStreamWriter(
        new java.io.FileOutputStream(OUT_JSON), "UTF-8"));
    jw.print("{\n" + fields.join(",\n") + "\n}\n");
    jw.close();
    out.close();
    java.lang.System.exit(verdict === "pass" ? 0 : 4);

} catch (e) {
    log("FATAL: " + e);
    try { e.printStackTrace(out); } catch (ig) {}
    try {
        var ew = new java.io.PrintWriter(new java.io.OutputStreamWriter(
            new java.io.FileOutputStream(OUT_JSON), "UTF-8"));
        ew.print("{\n \"status\": \"verify_error\",\n \"case\": " + jstr(CASE)
                 + ",\n \"error\": " + jstr("" + e) + "\n}\n");
        ew.close();
    } catch (ig2) {}
    out.close();
    java.lang.System.exit(3);
}
