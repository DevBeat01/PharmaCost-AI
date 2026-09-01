import os
# 【必须放在最顶部！！！】镜像环境变量，放后面就失效
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

from sentence_transformers import SentenceTransformer

print("开始加载&下载 all‑MiniLM‑L6‑v2 ...")
# 从镜像下载
model = SentenceTransformer('all-MiniLM-L6-v2')
print("✅ 模型下载完成，开始保存到本地磁盘")

save_path = r"D:\ai_model\all-MiniLM-L6-v2"
model.save(save_path)
print(f"✅ 文件已经保存成功！路径：{save_path}")
