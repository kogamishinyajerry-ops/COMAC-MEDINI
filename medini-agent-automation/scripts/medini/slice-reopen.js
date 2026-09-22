// $EXPERIMENTAL$
// slice-reopen.js — P1 阶段 B：新 JVM 重开 .fta → 回读语义摘要 + 重新计算 Q1。
// 本进程不导入 XML、不写模型，只从磁盘加载阶段 A 保存的 .fta。
// 占位符：__CASE__ __K_MAX__ __OUT_JSON__ __RUN_LOG__ __LOAD_URI__ __LOAD_PATH__
var CASE = "__CASE__";
var K_MAX = __K_MAX__;
var OUT_JSON = "__OUT_JSON__";
var RUN_LOG = "__RUN_LOG__";
var LOAD_URI = "__LOAD_URI__";
var LOAD_PATH = "__LOAD_PATH__";

var out = new java.io.PrintWriter(new java.io.FileWriter(RUN_LOG));
function log(s) { out.println(s); out.flush(); }
function sn(v) { return v == null ? "" : ("" + v); }
function fin(v) { return v == null ? "null" : v.toPlainString(); }
function jstr(s) { return JSON.stringify("" + s); }

function sha256hex(s) {
    var md = java.security.MessageDigest.getInstance("SHA-256");
    var d = md.digest(new java.lang.String(s).getBytes("UTF-8"));
    var sb = new java.lang.StringBuilder();
    for (var i = 0; i < d.length; i++) sb.append(java.lang.Integer.toHexString((d[i] & 0xff) | 0x100).substring(1));
    return sb.toString();
}
function fileSha256(path) {
    var md = java.security.MessageDigest.getInstance("SHA-256");
    var fis = new java.io.FileInputStream(path);
    var buf = java.lang.reflect.Array.newInstance(java.lang.Byte.TYPE, 8192);
    var n;
    while ((n = fis.read(buf)) > 0) { md.update(buf, 0, n); }
    fis.close();
    var d = md.digest();
    var sb = new java.lang.StringBuilder();
    for (var i = 0; i < d.length; i++) sb.append(java.lang.Integer.toHexString((d[i] & 0xff) | 0x100).substring(1));
    return sb.toString();
}

// 与 slice-save.js 完全一致的摘要算法（同一 canonical 定义是可比性的前提）
function canonical(model) {
    var L = [];
    var evs = [];
    for (var i = 0; i < model.events.size(); i++) {
        var e = model.events.get(i);
        evs.push("EV|" + sn(e.id) + "|" + sn(e.name) + "|" + sn(e.rawProbability) + "|" + sn(e.kind));
    }
    evs.sort(); L.push("EVENTS[" + evs.length + "]"); L = L.concat(evs);

    var gs = [];
    for (var i = 0; i < model.gates.size(); i++) {
        var g = model.gates.get(i);
        gs.push("GT|" + sn(g.name) + "|" + sn(g.kind));
    }
    gs.sort(); L.push("GATES[" + gs.length + "]"); L = L.concat(gs);

    var ns = [];
    for (var i = 0; i < model.eventNodes.size(); i++) {
        var n = model.eventNodes.get(i);
        var evid = "<na>";
        try { if (n.event != null) evid = sn(n.event.id); } catch (e) {}
        ns.push("EN|" + evid + "|" + sn(n.occurrence));
    }
    ns.sort(); L.push("NODES[" + ns.length + "]"); L = L.concat(ns);

    var cs = [];
    for (var i = 0; i < model.connections.size(); i++) {
        var c = model.connections.get(i);
        var src = "<na>", dst = "<na>";
        try { if (c.outputNode != null && c.outputNode.event != null) src = sn(c.outputNode.event.id); } catch (e) {}
        try {
            var inp = c.inputNode;
            if (inp != null) { dst = sn(inp.name); if (dst === "") dst = sn(inp.event != null ? inp.event.id : ""); }
        } catch (e) {}
        cs.push("CN|" + src + ">" + dst);
    }
    cs.sort(); L.push("CONNS[" + cs.length + "]"); L = L.concat(cs);
    return L.join("\n");
}

function computeQ(model, K) {
    var EcoreUtil = bind("org.eclipse.emf.ecore", "org.eclipse.emf.ecore.util.EcoreUtil", false);
    var tleNode = null, nodes = model.eventNodes;
    for (var i = 0; i < nodes.size(); i++) {
        var en = nodes.get(i), o = en.outputs;
        if (o == null || o.size() === 0) {
            if (tleNode == null) tleNode = en;
            else if (sn(en.name) < sn(tleNode.name)) tleNode = en;
        }
    }
    if (tleNode == null) throw "TLE not found in reopened model";
    var epp = EcoreUtil.create(Metamodel.FTA.EventProbabilityParameters);
    epp.missionTime = new java.math.BigDecimal(1);
    model.eventProbabilityParameters = epp;
    var AnalysisOptions = bind("de.ikv.medini.editor.fta.cockpit", "de.ikv.medini.editor.fta.operations.AnalysisOptions", false);
    var builder = AnalysisOptions.newBuilder();
    builder.cutSetLength(java.lang.Integer.valueOf(K));
    builder.eventProbabilityParameters(epp);
    var opts = builder.build();
    var BAMO = bind("de.ikv.medini.editor.fta.cockpit", "de.ikv.medini.editor.fta.operations.BuildAnalysisModelOperation", false);
    var am = EcoreUtil.create(Metamodel.FTA.AnalysisModel);
    var supplier = new java.util.function.Supplier({ get: function() { return am; } });
    var op = BAMO.of(null, tleNode, supplier, opts);
    var st = op.doExecute(progressMonitor, null);
    if (!st.isOK()) throw "BAMO failed on reopened model: " + st.getMessage();
    return am;
}

var fields = [];
function kv(k, v) { fields.push(" " + jstr(k) + ": " + v); }
function kvs(k, v) { fields.push(" " + jstr(k) + ": " + jstr(v)); }

try {
    log("== PHASE B: reopen from disk ==");
    var src = new java.io.File(LOAD_PATH);
    if (!src.exists()) throw "saved .fta missing: " + LOAD_PATH;
    var inSize = src.length();
    var inSha = fileSha256(LOAD_PATH);
    log("loaded file size=" + inSize + " sha256=" + inSha);

    var EmfURI = bind("org.eclipse.emf.common", "org.eclipse.emf.common.util.URI", false);
    var ResSet = bind("org.eclipse.emf.ecore", "org.eclipse.emf.ecore.resource.impl.ResourceSetImpl", false);
    var rs = new ResSet();
    // 与阶段 A 对称：file: URI 直接读磁盘（不依赖 workspace 已导入工程）
    var res = rs.getResource(EmfURI.createFileURI(LOAD_PATH), true);   // true = 触发 load
    log("resource loaded: " + ("" + res) + " contents=" + res.getContents().size());

    var model = res.getContents().get(0);
    log("root eClass-ish: " + sn(("" + model).substring(0, 80)));
    log("counts: events=" + model.events.size() + " gates=" + model.gates.size()
        + " nodes=" + model.eventNodes.size() + " conns=" + model.connections.size());

    var canon1 = canonical(model);
    var dg1 = sha256hex(canon1);
    log("semantic_digest(B) = " + dg1);

    var am1 = computeQ(model, K_MAX);
    var Q1 = fin(am1.unavailability);
    log("Q1 = " + Q1 + " cutsets=" + am1.cutSets.size());

    kv("status", jstr("ok"));
    kvs("case", CASE);
    kv("phase", jstr("reopen"));
    kv("Q1", jstr(Q1));
    kv("cutsets1", am1.cutSets.size());
    kvs("semantic_digest", dg1);
    kvs("semantic_canonical", canon1);
    kv("counts", "{\"events\": " + model.events.size() + ", \"gates\": " + model.gates.size()
        + ", \"eventNodes\": " + model.eventNodes.size() + ", \"connections\": " + model.connections.size() + "}");
    kvs("loaded_uri", "" + res.getURI());
    kvs("loaded_path", LOAD_PATH);
    kv("loaded_size", inSize);
    kvs("loaded_sha256", inSha);

    var jw = new java.io.PrintWriter(new java.io.OutputStreamWriter(new java.io.FileOutputStream(OUT_JSON), "UTF-8"));
    jw.print("{\n" + fields.join(",\n") + "\n}\n");
    jw.close();
    out.close();
    java.lang.System.exit(0);
} catch (e) {
    log("FATAL: " + e);
    try { e.printStackTrace(out); } catch (ig) {}
    try {
        var ew = new java.io.PrintWriter(new java.io.OutputStreamWriter(new java.io.FileOutputStream(OUT_JSON), "UTF-8"));
        ew.print("{\n \"status\": \"reopen_error\",\n \"case\": " + jstr(CASE) + ",\n \"error\": " + jstr("" + e) + "\n}\n");
        ew.close();
    } catch (ig2) {}
    out.close();
    java.lang.System.exit(3);
}
