"""能力1 验证: DataSearcher 真实检索 NCBI GEO.

需求: "拟南芥叶片单细胞数据，包含气孔细胞"
预期: 真实返回 GSE 记录 + 下载链接, 耗时应秒级.
"""
import sys, os, time, json
sys.path.insert(0, os.path.expanduser("~/bioinfo/agent_framework"))
from omicagent.data_searcher import DataSearcher

def main():
    t0 = time.time()
    ds = DataSearcher()
    query = "拟南芥叶片单细胞数据，包含气孔细胞 (Arabidopsis leaf single cell RNA-seq, guard cell)"
    report = ds.search(query, topk=5)
    print(f"\n===== 能力1: 数据检索 (耗时 {report.elapsed:.1f}s) =====")
    print(f"解析: species={report.parsed.get('species')} modality={report.parsed.get('modality')} tissue={report.parsed.get('tissue')}")
    print(f"尝试源: {report.sources_tried}, 可用源: {report.sources_ok}")
    print(f"记录数: {len(report.records)}")
    print("\n--- 前5条 ---")
    for r in report.records[:5]:
        print(f"[{r.source_db}] {r.accession} | rel={r.relevance:.2f} | {r.species} | n={r.n_samples}")
        print(f"  title: {r.title[:90]}")
        print(f"  download: {r.download_url}")
        if r.pubmed_id: print(f"  pubmed: {r.pubmed_id}")
    # 保存
    out = os.path.expanduser("~/bioinfo/agent_framework/results/search_report.json")
    with open(out, "w") as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
    print(f"\n报告已存: {out}")
    assert report.records, "未检索到任何记录"
    assert any(r.source_db == "GEO" for r in report.records), "GEO 主通道无结果"
    print("\n能力1 验证通过 ✅")

if __name__ == "__main__":
    main()
