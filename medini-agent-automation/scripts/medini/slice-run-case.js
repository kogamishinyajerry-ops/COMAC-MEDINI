// $EXPERIMENTAL$
// slice-run-case.js — A线纵向切片模板（基于 2026-08-19 已验证的 run-case.js v4 通道）。
// 占位符：__XML_PATH__（FaultTreePlus XML 绝对路径）、__OUT_JSON__（结果 JSON 绝对路径）、
//         __RUN_LOG__（运行日志）、__K_MAX__（割集阶上限）、__CASE__（案例名）
var CASE = "__CASE__";
var K_MAX = __K__;
var XML_PATH = "__XML_PATH__";
var OUT_JSON = "__OUT_JSON__";
var RUN_LOG = "__RUN_LOG__";

var out = new java.io.PrintWriter(new java.io.FileWriter(RUN_LOG));
function log(s) { out.println(s); out.flush(); }
function fin(v) { return v == null ? "null" : v.toPlainString(); }

try {
    log("case=" + CASE + " k=" + K_MAX);

    // ---- 1. detached FTAModel ----
    var EcoreUtil = bind("org.eclipse.emf.ecore", "org.eclipse.emf.ecore.util.EcoreUtil", false);
    var ftaModel = EcoreUtil.create(Metamodel.FTA.FTAModel);
    log("detached FTAModel ok");

    // ---- 2. unmarshal + import ----
    var XmlUtils = bind("de.ikv.medini.metamodel.faulttreeplus", "de.ikv.medini.metamodel.faulttreeplus.ftminimal.util.FaultTreePlusXmlUtils", false);
    var fis = new java.io.FileInputStream(XML_PATH);
    var xmlExport = XmlUtils.unmarshal(fis);
    fis.close();
    var Importer = bind("de.ikv.analyze.faulttreeplus", "de.ikv.analyze.faulttreeplus.importer.FaultTreePlusImporter", false);
    var stImp = Importer.addToFTAModel(xmlExport, ftaModel);
    log("import sev=" + stImp.getSeverity() + " msg=" + stImp.getMessage());
    log("model: events=" + ftaModel.events.size() + " gates=" + ftaModel.gates.size() + " conns=" + ftaModel.connections.size());

    // ---- 3. TLE = 无输出事件节点 ----
    var tleNode = null;
    var nodes = ftaModel.eventNodes;
    for (var ni = 0; ni < nodes.size(); ni++) {
        var en = nodes.get(ni);
        var o = en.outputs;
        if (o == null || o.size() == 0) {
            if (tleNode == null) tleNode = en;
            else if (String(en.name) < String(tleNode.name)) tleNode = en;
        }
    }
    log("TLE = " + (tleNode == null ? "null" : tleNode.name));
    if (tleNode == null) throw "TLE not found";

    // ---- 4. 概率参数：固定概率语义（missionTime=1，不隐式转换语义）----
    var epp = EcoreUtil.create(Metamodel.FTA.EventProbabilityParameters);
    epp.missionTime = new java.math.BigDecimal(1);
    ftaModel.eventProbabilityParameters = epp;

    // ---- 5. 分析选项 ----
    var AnalysisOptions = bind("de.ikv.medini.editor.fta.cockpit", "de.ikv.medini.editor.fta.operations.AnalysisOptions", false);
    var builder = AnalysisOptions.newBuilder();
    builder.cutSetLength(java.lang.Integer.valueOf(K_MAX));
    builder.eventProbabilityParameters(epp);
    var opts = builder.build();

    // ---- 6. BAMO 计算（doExecute 直通，跳过事务层）----
    var BAMO = bind("de.ikv.medini.editor.fta.cockpit", "de.ikv.medini.editor.fta.operations.BuildAnalysisModelOperation", false);
    var analysisModel = EcoreUtil.create(Metamodel.FTA.AnalysisModel);
    var supplier = new java.util.function.Supplier({ get: function() { return analysisModel; } });
    var op = BAMO.of(null, tleNode, supplier, opts);
    var st = op.doExecute(progressMonitor, null);
    log("compute sev=" + st.getSeverity() + " ok=" + st.isOK());
    if (!st.isOK()) {
        var ex = st.getException();
        if (ex != null) { log("exception: " + ex); ex.printStackTrace(out); }
        var ex2 = ex == null ? null : ex.getCause();
        while (ex2 != null) { log("caused by: " + ex2); ex2 = ex2.getCause(); }
        var ch = st.getChildren();
        for (var xi = 0; xi < ch.length; xi++) log("child: " + ch[xi].getMessage());
    }

    // ---- 7. 结果（EMF 属性语义）----
    var Q = analysisModel.unavailability;
    var cutsets = analysisModel.cutSets;
    log("Q_top = " + fin(Q) + "  cutsets = " + cutsets.size());

    var sb = new java.lang.StringBuilder();
    sb.append("{\n \"case\": \"").append(CASE).append("\",\n");
    sb.append(" \"engine\": \"medini\",\n");
    sb.append(" \"generated\": ").append(java.lang.System.currentTimeMillis()).append(",\n");
    sb.append(" \"Q_top\": ").append(fin(Q)).append(",\n");
    sb.append(" \"n_cutsets\": ").append(cutsets.size()).append(",\n \"cutsets\": [\n");
    for (var ci = 0; ci < cutsets.size(); ci++) {
        var cs = cutsets.get(ci);
        if (ci > 0) sb.append(",\n");
        sb.append("  {\"events\": [");
        var evs = cs.events;
        for (var ei = 0; ei < evs.size(); ei++) {
            if (ei > 0) sb.append(", ");
            sb.append("\"").append(evs.get(ei).id).append("\"");
        }
        sb.append("], \"unavailability\": ").append(fin(cs.unavailability)).append("}");
    }
    sb.append("\n ]\n}\n");
    var jw = new java.io.PrintWriter(new java.io.OutputStreamWriter(new java.io.FileOutputStream(OUT_JSON), "UTF-8"));
    jw.print(sb.toString());
    jw.close();
    log("JSON written: " + OUT_JSON);
    out.close();
    java.lang.System.exit(0);
} catch (e) {
    log("FATAL: " + e);
    try { e.printStackTrace(out); } catch (ignored) {}
    // 失败也写出标记文件（诚实区分"跑了但失败"与"没跑"）
    try {
        var ew = new java.io.PrintWriter(new java.io.OutputStreamWriter(new java.io.FileOutputStream(OUT_JSON), "UTF-8"));
        ew.print("{\n \"case\": \"" + CASE + "\",\n \"engine\": \"medini\",\n \"status\": \"script_error\",\n \"error\": " + JSON.stringify("" + e) + "\n}\n");
        ew.close();
    } catch (ignored2) {}
    out.close();
    java.lang.System.exit(3);
}
