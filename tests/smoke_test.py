"""框架冒烟测试: 验证 TaskPlanner + ReportGenerator 协同 (不执行重计算)."""
import sys, json, logging
sys.path.insert(0, ".")
from omicagent.task_planner import TaskPlanner
from omicagent.report_generator import ReportGenerator
from omicagent import config

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

print("=== 1. TaskPlanner 测试 ===")
planner = TaskPlanner()
plan = planner.plan("整合水稻与拟南芥叶片单细胞数据,输出跨物种整合UMAP与同源基因模块")
print("目标:", plan.goal)
for t in plan.tasks:
    print(f"  [{t.id}] tool={t.tool} module={t.module} | {t.description[:60]}")

print("\n=== 2. ReportGenerator 测试 (摘要+渲染) ===")
reporter = ReportGenerator()
summary = reporter.summarize(
    "跨物种整合测试: 水稻400细胞+拟南芥400细胞, 通过1:1同源基因Harmony整合, "
    "UMAP显示两物种细胞良好混合, 识别同源基因模块120个.", goal="跨物种整合")
print("摘要:", summary[:200])
out = reporter.render(
    title="OmicAgent 冒烟测试报告", goal="跨物种整合测试",
    summary=summary,
    tables=[{"title": "测试数据", "headers": ["物种","细胞数","来源"],
             "rows": [["水稻","400","GSE232863"],["拟南芥","400","integrated_leaf"]]}],
    run_info={"planner_model": config.COMPLEX_MODEL, "codegen_model": config.SIMPLE_MODEL,
              "llm_calls": reporter.llm.total_usage()["calls"],
              "total_tokens": reporter.llm.total_usage()["total_tokens"], "extra": {}})
print("报告:", out)
print("\nSMOKE_OK")
