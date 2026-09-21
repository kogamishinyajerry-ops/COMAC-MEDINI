// $EXPERIMENTAL$
// probe-model.js — 一次性结构探针：dump FTA 模型真实属性名与值（反射枚举 getter）。
// 目的：P1 保存/重开语义摘要的字段依据，避免凭猜测写摘要。
// 占位符：__XML_PATH__ __OUT_JSON__ __RUN_LOG__
var XML_PATH = "__XML_PATH__";
var OUT_JSON = "__OUT_JSON__";
var RUN_LOG = "__RUN_LOG__";

var out = new java.io.PrintWriter(new java.io.FileWriter(RUN_LOG));
function log(s) { out.println(s); out.flush(); }
function sn(v) { return v == null ? "null" : ("" + v); }

// Rhino 无法调用 EMF 接口方法（eClass/getClass 均不可见）——
// 但 EMF 生成的 Impl.toString() 会列出全部 structural feature，用它发现属性名。
function dumpProps(obj, label) {
    try { log("  " + label + " :: " + ("" + obj)); }
    catch (e) { log("  " + label + " :: <toString failed: " + e + ">"); }
}

function etype(obj) {
    // EMF toString 前缀形如 de.ikv...impl.LogicalGateImpl@hash —— 从中抽取 eClass 名
    try {
        var m = ("" + obj).match(/impl\.(\w+?)Impl@/);
        return m ? m[1] : "?";
    } catch (e) { return "?"; }
}

try {
    var EcoreUtil = bind("org.eclipse.emf.ecore", "org.eclipse.emf.ecore.util.EcoreUtil", false);
    var ftaModel = EcoreUtil.create(Metamodel.FTA.FTAModel);
    var XmlUtils = bind("de.ikv.medini.metamodel.faulttreeplus", "de.ikv.medini.metamodel.faulttreeplus.ftminimal.util.FaultTreePlusXmlUtils", false);
    var fis = new java.io.FileInputStream(XML_PATH);
    var xmlExport = XmlUtils.unmarshal(fis);
    fis.close();
    var Importer = bind("de.ikv.analyze.faulttreeplus", "de.ikv.analyze.faulttreeplus.importer.FaultTreePlusImporter", false);
    var stImp = Importer.addToFTAModel(xmlExport, ftaModel);
    log("import sev=" + stImp.getSeverity() + " msg=" + stImp.getMessage());
    log("counts: events=" + ftaModel.events.size() + " gates=" + ftaModel.gates.size()
        + " eventNodes=" + ftaModel.eventNodes.size() + " connections=" + ftaModel.connections.size());

    log("=== EVENTS ===");
    for (var i = 0; i < ftaModel.events.size(); i++) dumpProps(ftaModel.events.get(i), "EV" + i);
    log("=== GATES ===");
    for (var i = 0; i < ftaModel.gates.size(); i++) dumpProps(ftaModel.gates.get(i), "GT" + i + "(" + etype(ftaModel.gates.get(i)) + ")");
    log("=== EVENTNODES ===");
    for (var i = 0; i < ftaModel.eventNodes.size(); i++) dumpProps(ftaModel.eventNodes.get(i), "EN" + i + "(" + etype(ftaModel.eventNodes.get(i)) + ")");
    log("=== CONNECTIONS ===");
    for (var i = 0; i < ftaModel.connections.size(); i++) dumpProps(ftaModel.connections.get(i), "CN" + i + "(" + etype(ftaModel.connections.get(i)) + ")");

    // 序列化路径探测（保存用）
    log("=== SERIALIZABLE? ===");
    try {
        var EmfURI = bind("org.eclipse.emf.common", "org.eclipse.emf.common.util.URI", false);
        log("EmfURI ok, sample=" + EmfURI.createURI("platform:/resource/AUTO-WC/fta/_probe.fta"));
    } catch (e) { log("EmfURI FAIL: " + e); }

    var jw = new java.io.PrintWriter(new java.io.OutputStreamWriter(new java.io.FileOutputStream(OUT_JSON), "UTF-8"));
    jw.print("{\n \"status\": \"ok\",\n \"case\": \"probe\",\n");
    jw.print(" \"events\": " + ftaModel.events.size() + ", \"gates\": " + ftaModel.gates.size()
        + ", \"eventNodes\": " + ftaModel.eventNodes.size()
        + ", \"connections\": " + ftaModel.connections.size() + "\n}\n");
    jw.close();
    out.close();
    java.lang.System.exit(0);
} catch (e) {
    log("FATAL: " + e);
    try { e.printStackTrace(out); } catch (ig) {}
    out.close();
    java.lang.System.exit(3);
}
