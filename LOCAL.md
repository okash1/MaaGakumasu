# ローカル運用メモ (okash1 fork)

- `main`: 上流 SuperWaterGod/MaaGakumasu の main をそのまま追従する (独自コミットを乗せない)。
  更新: `git fetch upstream && git checkout main && git merge --ff-only upstream/main && git push origin main`
- `local/vX.Y.Z-jp-fixes`: 実行中のリリース版タグ (例 v1.5.2) から切った作業ブランチ。独自修正はここに積む。
  リリース版を上げるときは新タグから新ブランチを切り、必要な修正を cherry-pick / rebase する。
- 実行環境 (リリース zip を展開したもの) は `Z:\gakumasu-bot\MaaGakumasu`。バイナリは git 管理しない。
- ソース → 実行環境への反映: `python -I tools/sync_runtime.py Z:\gakumasu-bot\MaaGakumasu`
  (agent/, resource/, tasks/, lang/, data/, interface.json を上書きコピー。削除ファイルは消えない)
- 反映後は MaaGakumasu のタスクを開始し直せばリソースは再読込される。agent (python) 側の変更は GUI 再起動が確実。
- 上流に出せそうな修正は小さく保ち、Issue/PR として送る。
