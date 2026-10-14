def markdown_report(report):
    lines = [f"# {report['title']}", "", "## 会议摘要", "", report["summary"]["summary"], "", "## 决议", ""]
    for decision in report["summary"]["decisions"]:
        lines.extend([f"- {decision['text']}", f"  原文：{decision['evidence']['quote']}"])
    lines.extend(["", "## 行动项", ""])
    for item in report["actions"]:
        lines.extend([f"- {item['task']}；负责人：{item['owner'] or '待确认'}；期限：{item['due'] or '待确认'}", f"  原文：{item['evidence']['quote']}"])
    lines.extend(["", "## 取消安排", ""])
    lines.extend(f"- {item['task']}" for item in report.get("cancelled_actions", []))
    lines.extend(["", "## 复核", ""] + [f"- {issue}" for issue in report["review"]["issues"]])
    lines.extend(["", "## 运行信息", "", f"处理耗时：{report['metrics']['processing_ms']} ms；降级调用：{report['metrics']['fallback_agents']}；源版本：{report['source_version']}"])
    return "\n".join(lines) + "\n"
