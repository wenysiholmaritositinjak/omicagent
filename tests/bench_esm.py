#!/usr/bin/env python
"""ESM 速度基准: 决定 SATURN(主方案) 在 CPU 上是否可行."""
import time, torch, esm
torch.set_num_threads(8)
print("加载 esm2_t12_35M ...")
t0 = time.time()
model, alphabet = esm.pretrained.esm2_t12_35M_UR50D()
batch_converter = alphabet.get_batch_converter()
model.eval()
print(f"加载耗时 {time.time()-t0:.1f}s")
# 模拟 50 个长度 400 的蛋白
data = [(f"gene{i}", "MKL"*130 + "V") for i in range(50)]
batch_labels, batch_strs, batch_tokens = batch_converter(data)
t0 = time.time()
with torch.no_grad():
    out = model(batch_tokens, repr_layers=[12], return_contacts=False)
dt = time.time() - t0
emb = out["representations"][12]
print(f"50 蛋白(400aa) 耗时 {dt:.2f}s -> {50/dt:.1f} 蛋白/s")
# 推算 4000 / 60000 蛋白耗时
for n in (4000, 60000):
    print(f"  估算 {n} 蛋白: {n*dt/50/60:.1f} min")
