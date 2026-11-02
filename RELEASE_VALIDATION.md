# 验证

历史验证数据见 references/historical_release_validation.md，不能作为本版结果。

复跑：`python -m pytest -q`。需要打包复验时：`python tools/build_pack.py --out dist --kind full`，再 `python tools/verify_pack.py dist/<生成的zip>`。
