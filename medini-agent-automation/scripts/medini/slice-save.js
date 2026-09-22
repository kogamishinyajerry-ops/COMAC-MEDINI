// $EXPERIMENTAL$
// slice-save.js — P1 阶段 A：导入 → 计算 Q0 → 语义摘要 → 保存 .fta 到工作副本工程。
// 阶段 A 与阶段 B 是两个独立 JVM 进程，构成真正的"关闭→重开"。
// 占位符：__CASE__ __K_MAX__ __XML_PATH__ __OUT_JSON__ __RUN_LOG__ __SAVE_URI__ __SAVE_PATH__
var CASE = "__CASE__";
var K_MAX = __K_MAX__;
var XML_PATH = "__XML_PATH__";
var OUT_JSON = "__OUT_JSON__";
var RUN_LOG = "__RUN_LOG__";
var SAVE_URI = "__SAVE_URI__";     // platform:/resource/<PROJ>/fta/<case>.fta
var SAVE_PATH = "__SAVE_PATH__";   // 磁盘绝对路径（校验落盘）

var out = new java.io.PrintWriter(new java.io.FileWriter(RUN_LOG));
function log(s) { out.println(s); out.flush(); }
function sn(v) { return v == null ? "" : ("" + v); }
function fin(v) { return v == null ? "null" : v.toPlainString(); }
function jstr(s) { return JSON.stringify("" + s); }

function sha256hex(s) {
    var md = java.security.MessageDigest.getInstance("SHA-256");
    var b = new java.lang.String(s).getBytes("UTF-8");
    var d = md.digest(b);
    var sb = new java.lang.StringBuilder();
    for (var i = 0; i < d.length; i++) {
        sb.append(java.lang.Integer.toHexString((d[i] & 0xff) | 0x100).substring(1));
    }
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
    for (var i = 0; i < d.length; i++) {
        sb.append(java.lang.Integer.toHexString((d[i] & 0xff) | 0x100).substring(1));
    }
    return sb.toString();
}

// ---- 语义摘要：只含业务可见语义（id/名称/概率/门类型/拓扑），不含内存 identity ----
function canonical(model) {
    var L = [];
    var evs = [];
    for (var i = 0; i < model.events.size(); i++) {
        var e = model.events.get(i);
        evs.push("EV|" + sn(e.id) + "|" + sn(e.name) + "|" + sn(e.rawProbability) + "|" + sn(e.kind));
    }
    evs.sort();
    L.push("EVENTS[" + evs.length + "]"); L = L.concat(evs);

    var gs = [];
    for (var i = 0; i < model.gates.size(); i++) {
        var g = model.gates.get(i);
        gs.push("GT|" + sn(g.name) + "|" + sn(g.kind));
    }
    gs.sort();
    L.push("GATES[" + gs.length + "]"); L = L.concat(gs);

    var ns = [];
    for (var i = 0; i < model.eventNodes.size(); i++) {
        var n = model.eventNodes.get(i);
        var evid = "<na>";
        try { if (n.event != null) evid = sn(n.event.id); } catch (e) {}
        ns.push("EN|" + evid + "|" + sn(n.occurrence));
    }
    ns.sort();
    L.push("NODES[" + ns.length + "]"); L = L.concat(ns);

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
    cs.sort();
    L.push("CONNS[" + cs.length + "]"); L = L.concat(cs);
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
    if (tleNode == null) throw "TLE not found";
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
    if (!st.isOK()) throw "BAMO failed: " + st.getMessage();
    return am;
}

var fields = [];
function kv(k, v) { fields.push(" " + jstr(k) + ": " + v); }
function kvs(k, v) { fields.push(" " + jstr(k) + ": " + jstr(v)); }

try {
    log("== PHASE A: import + compute + save ==");
    var EcoreUtil = bind("org.eclipse.emf.ecore", "org.eclipse.emf.ecore.util.EcoreUtil", false);
    var ftaModel = EcoreUtil.create(Metamodel.FTA.FTAModel);
    var XmlUtils = bind("de.ikv.medini.metamodel.faulttreeplus", "de.ikv.medini.metamodel.faulttreeplus.ftminimal.util.FaultTreePlusXmlUtils", false);
    var fis = new java.io.FileInputStream(XML_PATH);
    var xmlExport = XmlUtils.unmarshal(fis);
    fis.close();
    var Importer = bind("de.ikv.analyze.faulttreeplus", "de.ikv.analyze.faulttreeplus.importer.FaultTreePlusImporter", false);
    var stImp = Importer.addToFTAModel(xmlExport, ftaModel);
    log("import sev=" + stImp.getSeverity() + " events=" + ftaModel.events.size()
        + " gates=" + ftaModel.gates.size() + " nodes=" + ftaModel.eventNodes.size()
        + " conns=" + ftaModel.connections.size());

    var am0 = computeQ(ftaModel, K_MAX);
    var Q0 = fin(am0.unavailability);
    log("Q0 = " + Q0 + " cutsets=" + am0.cutSets.size());

    // 摘掉游离的 EventProbabilityParameters：它是 computeQ 内 create() 出来的 detached
    // 对象（非 containment），直接 save 会报 "is not contained in a resource"。
    // 概率语义已由每个事件的 rawProbability 承载，参数对象不入盘。
    try {
        ftaModel.eventProbabilityParameters = null;
        log("unset eventProbabilityParameters (detached, not serializable)");
    } catch (e) {
        log("WARN unset eventProbabilityParameters failed: " + e);
    }

    var canon0 = canonical(ftaModel);
    var dg0 = sha256hex(canon0);
    log("semantic_digest(A) = " + dg0);

    // ---- 保存到工作副本工程 ----
    // 用 file: URI 而非 platform:/resource/<PROJ>/... —— 后者要求 Eclipse workspace
    // 已导入该工程（-files 只提供 script 上下文，不建立 workspace 映射，实测报
    // "Resource '/<PROJ>' does not exist"）。file: URI 直落磁盘，语义等价且无副作用。
    var EmfURI = bind("org.eclipse.emf.common", "org.eclipse.emf.common.util.URI", false);
    var ResSet = bind("org.eclipse.emf.ecore", "org.eclipse.emf.ecore.resource.impl.ResourceSetImpl", false);
    var saveUri = EmfURI.createFileURI(SAVE_PATH);
    var rs = new ResSet();
    var res = rs.createResource(saveUri);
    res.getContents().add(ftaModel);
    res.save(null);
    log("saved uri=" + saveUri + " (declared " + SAVE_URI + ") resourceClass=" + ("" + res));

    var f = new java.io.File(SAVE_PATH);
    var exists = f.exists();
    var size = exists ? f.length() : -1;
    var fh = exists ? fileSha256(SAVE_PATH) : "";
    log("on-disk exists=" + exists + " size=" + size + " sha256=" + fh);

    kv("status", jstr("ok"));
    kvs("case", CASE);
    kv("phase", jstr("save"));
    kv("Q0", jstr(Q0));
    kv("cutsets0", am0.cutSets.size());
    kvs("semantic_digest", dg0);
    kvs("semantic_canonical", canon0);
    kv("counts", "{\"events\": " + ftaModel.events.size() + ", \"gates\": " + ftaModel.gates.size()
        + ", \"eventNodes\": " + ftaModel.eventNodes.size() + ", \"connections\": " + ftaModel.connections.size() + "}");
    kvs("saved_uri", "" + saveUri);
    kvs("saved_path", SAVE_PATH);
    kv("saved_exists", exists ? "true" : "false");
    kv("saved_size", size);
    kvs("saved_sha256", fh);

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
        ew.print("{\n \"status\": \"save_error\",\n \"case\": " + jstr(CASE) + ",\n \"error\": " + jstr("" + e) + "\n}\n");
        ew.close();
    } catch (ig2) {}
    out.close();
    java.lang.System.exit(3);
}
