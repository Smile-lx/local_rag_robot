# 环境重建记录（2026-10-25，PM 执行，需归档）

## 背景
上一会话（harness-resume-000337）在 Python 3.12 + 旧路径 D:\ai 下运行，本会话发现：
- 仓库已迁移至 E:\ai\ai；本机 py 启动器仅剩 **Python 3.10.0**（D:\python\python.exe），3.12 及其 site-packages 全部不存在
- 即 requirements.txt 声明的依赖全部丢失，需重装

## 安装内容（全部为 py -3（3.10.0）site-packages，无全局其他改动）
- 命令：`py -3 -m pip install -r requirements/47846874/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 120 --retries 5`（清华镜像，design.md 环境事实约定）
- 关键版本（与 design.md 记录一致）：chromadb 1.5.9 / sentence-transformers 6.2.0 / torch 2.14.1 / transformers 5.20.2 / fastapi 0.162.0 / uvicorn 0.54.0 / pytest 9.1.1 / python-dotenv 1.2.4 / requests 2.32.x（含传递依赖共 100+ 包，完整清单见同目录 env-install-2026-10-25.log）
- pip 由 21.2.3 升级 setuptools 57.4.0→84.2.1（pip 自身未升级）

## 模型预下载
- `BAAI/bge-small-zh-v1.5`（bge 中文 embedding，~100MB）经 HF_ENDPOINT=https://hf-mirror.com 下载至本机 HF 缓存（~/.cache/huggingface）

## 回滚/复现
- 复现：按上述命令重装即可；卸载：`py -3 -m pip uninstall -r requirements/47846874/requirements.txt -y`（不含传递依赖）
