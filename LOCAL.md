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

## CLI 操作 (GUI なしでタスク実行・ログ取得)

`tools/maacli.py` は interface.json を読み、GUI と同じ ADB 接続・リソース・agent 子プロセスでタスクを実行する。
ラッパー: `Z:\gakumasu-bot\maa.cmd` / `maa.ps1` (実行環境同梱の python を使う)。

```
maa devices                       # ADB デバイス一覧
maa tasks                         # タスクと選択肢・既定値
maa presets                       # プリセット (tasks/preset.json)
maa run --preset 一键日常          # プリセットをそのまま実行
maa run 社团活动 每日挑战            # 任意タスクを既定値で実行
maa run 每日挑战 --opt "每日挑战/挑战模式=最大值"   # 選択肢の上書き (checkbox はカンマ区切り、input は OPTION.field=値)
maa run ... --dry-run -v          # 解決後の pipeline_override だけ表示
maa stop                          # 実行中の run を安全に停止
maa status                        # GUI / CLI 実行 / デバイス状態
maa logs                          # 直近の CLI ログ   (--gui: GUI ログ, --maafw: debug/maafw.log, --grep RE)
maa screenshot [path]             # エミュレータのスクリーンショット
```

- CLI の実行ログ: `MaaGakumasu\logs\cli-<日時>.log`。失敗時の画面は `MaaGakumasu\debug\on_error\`。
- GUI がタスク実行中のときは CLI から同時に走らせない (GUI を開いたまま待機状態なら可)。
