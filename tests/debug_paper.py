import sys, os
sys.path.insert(0, os.path.expanduser("~/bioinfo/agent_framework"))
from omicagent.ncbi_client import NCBIClient
n = NCBIClient()
full = n.fetch_pubmed_fulltext("37540378")
print("全文长度:", len(full))
print("含 'data availability':", "data availability" in full.lower())
print("含 'GSE':", "GSE" in full)
print("含 'deposited':", "deposited" in full.lower())
print("--- 全文前1500字符 ---")
print(full[:1500])
