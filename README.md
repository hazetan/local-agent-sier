# Local-SIer — ローカル環境で自律稼働するマルチエージェントSIer基盤

自宅のGPUサーバーとエッジデバイス（Raspberry Pi等）上に，24時間365日自律的に要件定義・設計・実装・結合テスト・内部監査を遂行する**「ローカル完結型の仮想SIer組織」**を構築・運用するための Discord Bot 兼マルチエージェント基盤です．

常駐する高速なメインモデル（例: `Gemma4-26B`）が現場のオペレーターとして進行管理・ファイル編集・ビルド検証を担い，大規模な設計や難解なバグに直面した際には，エージェント自身の判断で子タスクを定義して重量級モデル（例: `Qwen3-Coder-Next`）へ作業を外注（`invoke_professional`）します．
**「オペレーターが自力で解決するか，プロフェッショナルへ外注するか」**という自律的な意思決定と開発プロセスを，Discordのライブモニターからリアルタイムに観測・統制できます．

---

## 1. プロジェクト概要・特徴

- **自律判断・子タスク外注型ハイブリッド推論基盤**:
  - 進行管理・通常のファイル編集・ビルド確認は，VRAM内に常駐するメインモデル（`MODEL_MAIN`）が高速に処理．
  - 大規模な新規実装や難解なコンパイルエラー・テスト失敗に直面した際は，エージェント自身の判断で明確な子タスクを定義し，`invoke_professional` ツールによって重量級モデル（`MODEL_HEAVY`）へ作業を外注．
  - 呼び出された Professional モデルは，VRAMスワップを防ぐ排他セッション下で**全ツール（ファイル読み書き・行単位編集・コマンド実行）を直接駆使し，最大 `PROFESSIONAL_MAX_STEPS`（例: 512ステップ）まで自律的に実装・ビルド・テスト検証を完遂**してから制御を返還します．
- **フルコンテキスト自動共有＆静的ガード機構**:
  - Professional 呼び出し時には，ワークスペース全体のディレクトリツリー・全ソースコード全文・直近の `run_command` 生出力（コンパイルエラーやテスト結果）を制限なしで自動共有し，モデル間の伝言ゲームによる情報欠落を防止．
  - C/C++の `#include` 競合を引き起こす**同名ファイルの重複検知（`DUPLICATE_FILENAME`）**や，**出力途中切断の自動検知（`TRUNCATED_OUTPUT`）**を備えています．
- **アイドル時の自律行動モード（Autonomous Initiative）**:
  - 未完了タスクが0件になると，AIが自らワークスペースのファイル整理整頓・不要ファイル削除・台帳更新を実施し，さらにコンパイラ警告フラグの厳格化やファジング／ストレステスト追加などの新規 `[TODO]` タスクを自己起票して開発サイクルを自律継続します．
- **4つの標準ドキュメントによる単一真実源（SSOT）**:
  - 外部データベースを使用せず，ワークスペース（`~/`）直下の4つの標準ドキュメント（`README.md`, `TASK.md`, `DESIGN.md`, `REPORT.md`）のみを Single Source of Truth として工程分業で開発を継続します．
- **隔離されたDockerサンドボックス＆統合NAS（Web UI / WebDAV / Samba）**:
  - ビルド・テストは隔離された Docker サンドボックス環境内（`~/` = `/root`）で安全に実行．`Dockerfile.sandbox` の拡張により Python / C / C++ / MicroPython / OpenSCAD（3Dモデル `.stl` 生成）等に柔軟に対応可能．
  - ワークスペース全体を **NAS（Web UI / WebDAV / Samba）** として公開し，PC・スマートフォンから直接ファイルの閲覧・編集・取得が可能．

---

## 2. 主な機能

### 自律判断 × Professional外注パイプライン（SE / PG）
ミクロな自己判断（`complete_phase`）とマクロな無限推論ループを組み合わせた階層型アーキテクチャで動作します：
1. **通常進行・自力解決（メインオペレーターモデル）**:
   - `read_files`（一括行番号付き閲覧），`edit_lines`（行範囲の一括置換），`add_lines_after`（既存行を保持した行挿入），`write_files`（一括新規作成），`run_command`（サンドボックス内コマンド実行）を用いて自ら設計・実装・デバッグを遂行します．
2. **子タスク外注（`invoke_professional` による重量級モデル召喚）**:
   - オペレーターが大規模実装や複雑な不具合に直面すると，子タスク名（`subtask_title`）と達成条件（`instruction`）を定義して Professional モデルへ外注します．
   - システムが自動的に軽量モデルをアンロードしてVRAMを開放し，Professional モデルを排他ロック下で常駐させます．
3. **最大 `PROFESSIONAL_MAX_STEPS` の自律完遂と帰還**:
   - Professional モデルはオペレーターと同一の全ツールを用いて「コード作成・編集 ➔ `run_command` でビルド＆テスト ➔ エラー箇所を `read_file` で確認 ➔ `edit_lines` / `add_lines_after` で修正」を最大512ステップまで高速に反復し，完了条件を満たした時点で `complete_phase` を呼び出してオペレーターへ成果を返却します．

### 自律型 SIer 5役職パイプライン
- **PM（プロジェクトマネージャー / 顧客窓口）**: `.env` で指定されたテキストチャンネル・フォーラムチャンネル（スレッドおよびテキスト添付ファイル対応）のメッセージに**メンション不要で自動反応**．`~/README.md` に正式プロジェクトパスを確定・記載した上で，`~/TASK.md` へ `[TODO]` を起票・ZIP出力・顧客応答を行います（過去のチャット履歴による汚染を防ぐため，常に最新のドキュメント状態と今回の発言のみを参照）．
- **SE（システムエンジニア / アーキテクト＆自律メンテナンス）**: `[TODO]` タスクを調査し，自らまたは `invoke_professional` を用いて数理モデル・データ構造・テスト仕様を網羅した `~/DESIGN.md` を策定し，`[IMPL]` へ遷移させます．
- **PG（プログラマー / 実装＆極限ビルド検証）**: `~/DESIGN.md` に基づき，自力でのツール操作と `invoke_professional` への子タスク外注を使い分けて製品コードと自動テストを構築．`-Wall -Wextra -Werror -Wpedantic -Wconversion` 等でのゼロ警告ビルドとテスト通過を確認して `[QA]` へ遷移させます．
- **QA（品質保証 / 極限検証・破壊的テスト）**: `[QA]` タスクを Docker サンドボックス内で厳格に検証（境界値・異常入力・ファジング・連続負荷テスト）．`TODO` 放置や警告・仕様不整合が1箇所でもあればファイル名・行番号付きで `[FIX]` に差し戻し，全テスト合格時のみ `[DONE]` として `~/README.md` を更新します．
- **AUDITOR（PMO / 内部監査）**: 各部署の稼働ログ（`logs/*.log`）と4大ドキュメントを定期監視し，SSOT違反や形骸化テスト，差戻しループの打開策を `~/REPORT.md` に出力します．

### リアルタイム可視化モニター（Discord ANSIダッシュボード）
- **稼働状況モニターBox**: 指定チャンネルに固定サイズ（ANSIカラー対応）のダッシュボードを表示し，数秒おきに自動更新します（Bot停止時に自動削除）．
  - GPUホスト（Ollamaサーバー）のリアルな VRAM / RAM 消費量・空き容量（`tools/desktop_metrics_server.py` 連携，未設定時は `/api/ps` へフォールバック）および Bot ホストの空きメモリ
  - 各役職ごとの現在稼働状態（通常稼働 `▶` / Professional 外注稼働 `★ PG:PRO`）・メモリ消費割合・推論速度（`tok/s`）・コンテキスト長占有率
  - 過去・現在・未来のタスクパイプラインと，進行中タスクの実行ツール＆結果のブランチツリー
- **リアルタイム生ログBox (`log-raw`)**: 現在稼働中のモデルの Thinking（思考プロセス），実行ツール・引数，コマンド実行結果の生出力をステップごとにリアルタイムで固定枠内に表示します．

### 統合ワークスペース NAS
- `workspace/` を以下の3プロトコルで共有可能（Cloudflare Tunnel 等を介した外部公開にも対応）．
  - **Web UI**: ブラウザからファイル閲覧・コード編集・アップロード・新規作成・ZIPダウンロードが可能．
  - **WebDAV (`/dav/`)**: Windows / iOS / Android のファイルアプリからネットワークドライブとしてマウント可能．
  - **Samba (SMB)**: `.env` の `SAMBA_ENABLE=True` により，ローカルLAN向けに `\\<ホストIP>\workspace` でファイル共有が可能．

---

## 3. ディレクトリ構成

```text
.
├── .env.example                 # 環境変数テンプレート（全パラメータ集約・コード内デフォルト値なし）
├── Dockerfile.sandbox.example   # サンドボックス用Dockerfileテンプレート（C/C++/Python/組込み/CAD対応）
├── requirements.txt             # Python依存パッケージ一覧
├── setup.sh                     # 初回セットアップスクリプト
├── run.sh                       # Bot起動スクリプト
├── src/
│   ├── main.py                  # エントリーポイント
│   ├── agents/                  # 5役職エージェント，自律判断＆Professional外注ランナー，パイプライン制御
│   ├── core/                    # 設定管理, Ollama通信(通常/Pro排他セッション), 構造化ロガー, テレメトリ, NAS
│   ├── discord_app/             # Discord Bot本体, ライブステータス＆生ログBox監視
│   └── sandbox/                 # Dockerサンドボックス管理, 行単位編集ツール(edit_lines/add_lines_after), 検品機構
├── tools/
│   ├── desktop_metrics_server.py # GPU/LLMサーバー側の実VRAM/RAM取得用軽量HTTPサーバー
│   └── start_metrics.sh         # メトリクスサーバー起動スクリプト
├── workspace/                   # エージェントの作業領域（コンテナ内では ~/ にマウント）
├── exports/                     # 生成されたZIPアーカイブ出力先
└── logs/                        # 役職別・システム・エラーログ出力先（pm/se/pg/qa/auditor/error/system_debug.log）
```

---

## 4. 使用技術

- **言語**: Python 3.12+（Ruff Linter 準拠）
- **フレームワーク・ライブラリ**: `discord.py`, `aiohttp`, `python-dotenv`, `ddgs`
- **インフラ・環境**:
  - Docker（コード実行サンドボックス ＆ Alpineベース Samba コンテナ）
  - 組み込み WebUI / WebDAV サーバー（`aiohttp`）
- **LLMバックエンド**: Ollama ネイティブ `/api/chat`（Thinking モード・ネイティブTool Calling＋テキスト構文フォールバック抽出・動的モデルアンロード＆排他セッション制御）および `/api/ps`
- **外部API**: DuckDuckGo（Web検索・Webページ取得）

---

## 5. セットアップ手順

1. **Docker のインストールと権限設定**
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER
   ```
   *(反映のため一度再ログインしてください)*

2. **セットアップスクリプトの実行**
   仮想環境の作成，依存ライブラリのインストール，`.env` および `Dockerfile.sandbox` の雛形生成，Dockerイメージのビルドを一括で行います．
   ```bash
   chmod +x setup.sh run.sh tools/start_metrics.sh
   ./setup.sh
   ```

3. **環境変数（`.env`）の設定**
   本システムはコード内にハードコードされたデフォルト値を持たず，すべてのパラメータを `.env` で一元管理します．生成された `.env` を編集し，ご自身の環境に合わせて7つのセクションを設定してください．
   - **1. Discord Bot & Live Monitor Channels**:
     - `DISCORD_TOKEN` : Discord Bot トークン
     - `DISCORD_CHANNEL_IDS` : 自動反応するテキスト／フォーラムチャンネルID（カンマ区切り）
     - `DISCORD_STATUS_CHANNEL_ID` / `DISCORD_RAW_LOG_CHANNEL_ID` : 監視Box表示用チャンネルID
   - **2. Ollama Server, Streaming & Context Budgets**:
     - `OLLAMA_BASE_URL` : Ollama サーバーのURL（例: `http://192.168.x.x:11434`）
     - `LLM_TIMEOUT`, `OLLAMA_NUM_PARALLEL`, `STREAM_GRANULARITY_TOKENS`, `IO_CHUNK_BYTES`
     - `CTX_CHAR_BUDGET_LIGHT` / `CTX_CHAR_BUDGET_HEAVY` : Python側のメッセージ・ファイル読み込み文字数予算
   - **3. Light Models (Main / Scout & Operator)**:
     - `MODEL_MAIN`（および役職別 `MODEL_PM`, `MODEL_SE`, `MODEL_PG`, `MODEL_QA`, `MODEL_AUDITOR`）: 常駐オペレーターモデル名（例: `Gemma4-26B-it-ab:Q4`）
     - `OLLAMA_THINK` / `THINK_PROMPT_BOOST` / `OLLAMA_KEEP_ALIVE` : Thinking モード・常駐時間設定
     - `SERVER_CTX_SIZE` / `MAX_TOKEN` : コンテキスト長・最大出力トークン
   - **4. Professional / Heavy Models (Outsourced Sub-Agent)**:
     - `MODEL_HEAVY`（および任意で `MODEL_HEAVY_SE`, `MODEL_HEAVY_PG`）: 子タスク外注先の重量級モデル名（例: `Qwen3-Coder-Next:Q4_K_M`）
     - `PROFESSIONAL_MAX_STEPS` : 1回の子タスク外注（`invoke_professional`）で自律実行できる最大ステップ数（例: `512`）
     - `HEAVY_CTX_SIZE` / `HEAVY_MAX_TOKEN` / `HEAVY_THINK` / `HEAVY_KEEP_ALIVE`（連続ステップ稼働中の再ロード防止のため `30m` 等を推奨，セッション完了時は自動アンロード）
   - **5. Pipeline Loop Limits & Autonomous Initiative Mode**:
     - `MAX_STEPS_PER_PHASE`, `PIPELINE_IDLE_SEC`, `AUDITOR_INTERVAL_SEC`
     - `AUTONOMOUS_ENABLE` / `AUTONOMOUS_IDLE_SEC` / `AUTONOMOUS_MAX_STEPS` : アイドル時の自律整理整頓・自己タスク起票設定
   - **6. Docker Sandbox & Workspace Directories**:
     - `DOCKER_IMAGE`, `DOCKER_MEM_LIMIT`, `COMMAND_TIMEOUT`, `WORKSPACE_DIR`, `EXPORTS_DIR`, `LOG_DIR`
     - `RESET_WORKSPACE_ON_STARTUP` : 起動時のワークスペース初期化フラグ
     - `SANDBOX_ENV_HINT` : サンドボックス内の利用可能ツールをエージェントへ伝えるヒント文
   - **7. Hardware Telemetry & Workspace NAS**:
     - `DESKTOP_VRAM_GB`, `DESKTOP_RAM_GB`, `DESKTOP_METRICS_URL`（例: `http://192.168.x.x:11435/metrics`）
     - `NAS_ENABLE`, `NAS_PORT`, `NAS_USER`, `NAS_PASSWORD`, `SAMBA_ENABLE`, `SAMBA_PORT`

4. **（任意）GPU / Ollama サーバー側でメトリクスサーバーを起動**
   Bot と別マシンで Ollama を動かしており，正確な VRAM / RAM 実測値をモニターに表示したい場合は，GPU サーバー側で以下を実行します．
   ```bash
   ./tools/start_metrics.sh
   ```

5. **Bot の起動**
   `tmux` 等の上で以下を実行して起動します．
   ```bash
   ./run.sh
   ```