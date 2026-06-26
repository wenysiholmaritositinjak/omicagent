#!/usr/bin/env Rscript
# 探查两个 Seurat Rds: 物种/维度/元数据, subset 到 N 细胞并导出 MatrixMarket+meta
suppressPackageStartupMessages({ library(Seurat); library(Matrix) })

args <- commandArgs(trailingOnly = TRUE)
n_cells <- as.integer(if (length(args) >= 1) args[1] else 500)
out_root <- if (length(args) >= 2) args[2] else "~/bioinfo/data/subsets"
dir.create(out_root, showWarnings = FALSE, recursive = TRUE)

# 用户指定: file1(GSE232863)=rice, file2(integrated_leaf)=arabidopsis
SPECIES_MAP <- c(file1 = "rice", file2 = "arabidopsis")

get_counts <- function(obj) {
  # 优先 RNA assay
  asys <- tryCatch(Assays(obj), error = function(e) character(0))
  cat("  assays:", paste(asys, collapse = ","), "\n")
  if ("RNA" %in% asys) return(GetAssayData(obj, assay = "RNA", layer = "counts"))
  if ("data" %in% asys) return(GetAssayData(obj, assay = "data", layer = "counts"))
  DefaultAssay(obj) <- asys[1]
  GetAssayData(obj, layer = "counts")
}

process <- function(path, label, species) {
  cat(sprintf("\n===== %s (%s) : %s =====\n", label, species, path))
  t0 <- Sys.time()
  obj <- tryCatch(readRDS(path), error = function(e) { cat("读取失败:", e$message, "\n"); return(NULL) })
  if (is.null(obj)) return(invisible(NULL))
  cat("读取耗时:", round(as.numeric(difftime(Sys.time(), t0, units = "secs")), 1), "s\n")
  cat("class:", paste(class(obj), collapse = ","), "\n")
  cat("dim:", nrow(obj), "features x", ncol(obj), "cells\n")
  feats <- rownames(obj)
  cat("feature 示例(前15):", paste(head(feats, 15), collapse = ", "), "\n")
  cat("feature 示例(后5):", paste(tail(feats, 5), collapse = ", "), "\n")
  cat("meta 列:", paste(colnames(obj@meta.data), collapse = ", "), "\n")
  red <- tryCatch(Reductions(obj), error = function(e) character(0))
  cat("reductions:", paste(red, collapse = ","), "\n")

  set.seed(42)
  nsub <- min(n_cells, ncol(obj))
  cells <- sample(colnames(obj), nsub)
  sub <- obj[, cells]
  mat <- get_counts(sub)
  out <- file.path(out_root, species)
  dir.create(out, showWarnings = FALSE, recursive = TRUE)
  writeMM(mat, file.path(out, "matrix.mtx"))
  writeLines(rownames(mat), file.path(out, "features.tsv"))
  writeLines(colnames(mat), file.path(out, "barcodes.tsv"))
  write.csv(obj@meta.data[cells, , drop = FALSE], file.path(out, "metadata.csv"), row.names = FALSE)
  saveRDS(sub, file.path(out, "subset.rds"))
  cat(sprintf("已保存到 %s : %d genes x %d cells\n", out, nrow(mat), ncol(mat)))
  rm(obj, sub, mat); gc(verbose = FALSE)
  invisible(NULL)
}

f1 <- "/mnt/c/Users/31159/Desktop/rice_reference/At_GA/GSE232863_scRNA_RNA_leaf.Rds"
f2 <- "/mnt/c/Users/31159/Desktop/rice_reference/At_GA/integrated_leaf.rds"
process(f1, "file1_GSE232863", SPECIES_MAP["file1"])
process(f2, "file2_integrated_leaf", SPECIES_MAP["file2"])
cat("\n===== 导出汇总 =====\n")
for (sp in c("rice", "arabidopsis")) {
  d <- file.path(out_root, sp)
  if (dir.exists(d)) cat(sp, ":", list.files(d), "\n")
}
cat("INSPECT_DONE\n")
