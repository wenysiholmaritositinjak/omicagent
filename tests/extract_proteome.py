#!/usr/bin/env python
"""从 GTF + genome.fasta 提取蛋白序列.
- 若 GTF 含 CDS: 直接拼接 CDS 翻译 (拟南芥 Araport)
- 若 GTF 仅 exon/transcript (IRGSP): 拼接全部 exon 得 mRNA, 找最长 ORF 翻译
输出 {species}_proteins.fasta, header 为 gene_id. 每基因取一个转录本.
用法: python extract_proteome.py <gtf> <fasta> <out_fasta> <species>
"""
import sys, re
from collections import defaultdict
from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord

GTF, FASTA, OUT, SPECIES = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

def parse_attr(attr):
    return {m.group(1): m.group(2) for m in re.finditer(r'(\w+)\s+"([^"]+)"', attr)}

# tx_id -> list[(seqname,start,end,strand)]; gene_id per tx
exons = defaultdict(list)
cds = defaultdict(list)
tx_gene = {}
features_seen = set()
with open(GTF) as f:
    for line in f:
        if line.startswith("#") or not line.strip():
            continue
        p = line.rstrip("\n").split("\t")
        if len(p) < 9:
            continue
        seqname, feat, start, end, strand, attr = p[0], p[2], int(p[3]), int(p[4]), p[6], p[8]
        features_seen.add(feat)
        a = parse_attr(attr)
        tx = a.get("transcript_id")
        gene = a.get("gene_id") or tx
        if tx:
            tx_gene[tx] = gene
        if feat == "CDS" and tx:
            cds[tx].append((seqname, start, end, strand))
        elif feat == "exon" and tx:
            exons[tx].append((seqname, start, end, strand))

has_cds = bool(cds)
print(f"[{SPECIES}] features: {sorted(features_seen)} | 有CDS: {has_cds} | 转录本: {len(tx_gene)}")

genome = {}
for rec in SeqIO.parse(FASTA, "fasta"):
    genome[rec.id] = str(rec.seq).upper()
print(f"[{SPECIES}] 基因组序列数: {len(genome)}")

def assemble(parts):
    """拼接区间为序列 (按坐标排序, 反向链取反向互补)."""
    parts = sorted(parts, key=lambda x: (x[0], x[1]))
    seqname = parts[0][0]
    strand = parts[0][3]
    chrom = genome.get(seqname)
    if not chrom:
        return None, None
    s = "".join(chrom[st-1:en] for sq, st, en, sd in parts if sq == seqname)
    if strand == "-":
        s = str(Seq(s).reverse_complement())
    return s, seqname

def longest_orf_prot(seq):
    """从 mRNA 找最长 ORF 翻译 (IRGSP 无 CDS 时的回退)."""
    prots = []
    for frame in range(3):
        sub = seq[frame:]
        # 补齐到 3 的倍数
        sub = sub[: (len(sub) // 3) * 3]
        p = str(Seq(sub).translate())
        # 切分到终止子
        for seg in p.split("*"):
            seg = seg.strip()
            if len(seg) >= 30:
                prots.append(seg)
    return max(prots, key=len) if prots else None

records = []
seen_genes = set()
for tx in tx_gene:
    if tx in seen_genes:
        continue
    gene = tx_gene[tx]
    if has_cds and tx in cds:
        s, _ = assemble(cds[tx])
        if not s:
            continue
        prot = str(Seq(s[: (len(s) // 3) * 3]).translate())
        if prot.endswith("*"):
            prot = prot[:-1]
        if "*" in prot or len(prot) < 10:
            continue
    else:
        if tx not in exons:
            continue
        s, _ = assemble(exons[tx])
        if not s:
            continue
        prot = longest_orf_prot(s)
        if not prot or len(prot) < 30:
            continue
    if gene in seen_genes:
        continue
    seen_genes.add(gene)
    records.append(SeqRecord(Seq(prot), id=gene, description=f"{SPECIES}|{tx}"))

with open(OUT, "w") as f:
    SeqIO.write(records, f, "fasta")
print(f"[{SPECIES}] 写出蛋白: {len(records)} -> {OUT}")
