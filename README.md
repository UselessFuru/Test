# Raiden Companion — Windows starter 0.14

Qwen chooses live searches, reads pages, and selects relevant evidence inside the overlay. **SearXNG searches; Trafilatura reads general pages; Raiden checks build selections before displaying them.** Genshin builds now prefer the [Genshin Impact Helper Team](https://genshin-impact-helper-team.github.io/genshin-builds/en/), using a live reader for its ranked build fields. This workflow does not use Bionic's paid web search, fine-tune Qwen, or add individual characters to a database.

## Update and start

1. Quit your old Raiden instance from its system-tray menu. Extract this ZIP into a **new writable folder**. Keep the **entire RaidenCompanion folder** together; replacing only core.py/web_search.py will not install this update.
2. Keep your existing Python 3.12 and Qwen installation. Run **Start-Raiden.bat**. It checks the existing dependencies and starts the overlay. If version 0.12 already connected successfully, keep your current Qwen, Bionic and Docker installations. Your icon, hotkey, voice and model settings are retained. The Settings title ends with **0.14**.
3. Install [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) if needed. Choose **Windows AMD64**, start Docker Desktop with **Linux containers**, and let its engine finish starting. Docker may request Windows/WSL setup or a restart during installation.
4. Run **Start-Search.bat**. Its first run downloads the SearXNG software image and creates a local configuration secret. Subsequent starts reuse that installation. Keep Docker Desktop running. **This installs search software, not game guides.** Docker and its image do use disk space.
5. Keep Qwen loaded and the API server enabled in **Bionic → Settings → Local Model API**. Raiden's usual AI address remains `http://127.0.0.1:1234/v1`. Copy the exact model ID if automatic selection picks another model.
6. In **Raiden → Settings → Guides**, choose **Qwen + SearXNG + Trafilatura** and keep **SearXNG address** at `http://127.0.0.1:8888`. Click **Test search connection**. Leave Web on **Auto** or choose **Always** to explicitly require a lookup.
7. Run **Check-Connections.bat** for a full check on your PC. It tests search, real page extraction, and an actual local-model search → read → cited-answer sequence. Results appear in its terminal; no report or guide files are saved.

If Python is not installed, use [64-bit Python 3.12](https://www.python.org/downloads/windows/) with the Python launcher enabled. Start-Raiden also supports a Python on PATH. Run Start-Raiden before Start-Search so the latter can use the app's environment.

## What changed in 0.14

This fixes the Hsin heading-only answer and the rejected Aemeth/Aemeath spelling. The default tool workflow searches for **any game**, using the current question and conversation to identify the subject. It does not need an entry for each character or a directory for each new game.

- General page tables now preserve item names, slot values and column relationships. Collapsible item cards retain their labels, including cards connected through accessible region labels. Duplicate flattened page text is no longer appended as orphaned headings.
- Navigation, table headers and base character stats cannot stand in for build recommendations. A full build must contain source-backed weapons/equipment and recommended stats before it counts as complete. If those core fields are missing, Qwen is asked to select more evidence, read another section/page or refine its search. Focused questions only require the fields requested.
- Qwen can select compact **evidence record IDs** instead of copying long paragraphs perfectly. Raiden renders the corresponding complete source text, with names, conditions and citations. New equipment terms can be classified by Qwen from their page context and shown under the source's heading.
- A one-letter spelling discrepancy can be recovered when multiple independent same-game guide titles agree and the fetched page confirms the name. Raiden tells you which spelling it used. Short or conflicting names are not silently replaced.
- Research has a larger bounded budget. If it still cannot verify every core field, it shows the supported portion and names the missing fields. Pre-release pages are labelled as provisional. Citation previews include the passages actually used in the answer.

The Genshin Helper Team live roster remains a preferred source for Genshin. Other games use the same general search/read tools. Existing source checks still reject invented labels, rewritten quotations, wrong-character/game pages and mixed preferred-source roles. Budget or owned-equipment questions require a source-backed model selection. No guide collection is installed or saved.

These checks establish where the displayed information came from; they cannot prove a guide author is correct or current. Ordinary questions, puzzles and other non-build answers still use Qwen's normal sourced prose. Source quotations remain in their original language so their names and qualifications are preserved.

## Try it

Ask `Nicole build in Genshin Impact`, then `what about Mavuika`, then `her best weapon?`. If Raiden asks which game, a reply such as `mb genshin impact` remains part of the conversation. For a different game, name it: `best build for Hsin in Wuthering Waves`, then `what about aemeth`. You can also ask for an unfamiliar game explicitly, for example `CHARACTER build in GAME`.

New games use the same search/read tools. Qwen is instructed to use the terminology in their sources and the current character, rather than assuming every game uses artifacts. There is no required per-character or per-game directory for this workflow. Readable, relevant pages must exist; a new game with no published guides cannot yield a verified build.

The status line shows search phrases and the site being read. Replies appear in the resizable chat panel, and numbered citations open page excerpts **inside Raiden**. The original-page browser button is optional. Automatic speech uses the existing voice settings after the final reply arrives.

| Setting | What it does |
| --- | --- |
| Web: Auto | Qwen can choose tools for any question. Recognized guides/current questions also receive an explicit live-evidence instruction. Ordinary questions can be answered locally. |
| Web: Always | Requires live evidence before factual advice; a clarification can come first. |
| Web: Off | Local model answers without these web tools. |
| Guide workflow: Qwen + SearXNG + Trafilatura | New default, including for existing installations that have no workflow preference. |
| Guide workflow: Classic lookup | Explicit fallback to version 0.11's app-directed retrieval. Its separate search-engine setting only affects this mode. |

For long guide research, try **16K model context** if memory permits. Shorter contexts may work for simple questions. Tool use can require several model generations, so it will be slower than one ordinary reply on your laptop. Keep **Fast Qwen replies** enabled; it requests non-thinking mode for Qwen3.5. Server/runtime support determines whether that setting is honored. The game, model, context and speech all compete for memory; no latency or game FPS is guaranteed.

## If something fails

- **Cannot reach SearXNG:** start Docker Desktop, run Start-Search.bat, and check port 8888 in Settings → Guides. Stop-Search.bat stops the search container without deleting its software/configuration.
- **SearXNG JSON forbidden (403):** `search/config/settings.yml` must include `json` under `search.formats`. The included configuration already does. Restart the search service after changing its settings.
- **Zero results:** the service is reachable but its upstream search engines may be unavailable or blocking requests. Retry later or review SearXNG's engine configuration. Raiden does not bypass access restrictions.
- **Page blocked or unreadable:** Qwen can read another result or refine the search. Some websites require scripts or a login, which this reader does not provide. Search snippets are not accepted as sufficient evidence for a build.
- **Tool syntax appears instead of calls:** update Bionic/LM Studio and its model runtime. The `/v1/chat/completions` response must contain `message.tool_calls`. Check-Connections.bat tests this on your loaded model. A port change alone does not enable tools.
- **Only reasoning / empty answer:** the connection worked but Qwen produced no final content or tool call. Check its Thinking setting/runtime. Private reasoning is never shown or spoken.
- **Context limit or slow generation:** reduce the scope of the question, start a new chat, or increase the model's loaded context if memory permits. A larger timeout permits a longer wait but does not speed up inference.

A question is bounded to at most six searches, ten page-read operations and sixteen model rounds. Long pages provide section positions for focused follow-up reads. Earlier tool messages are shortened to limit context growth. For builds, model selections must match complete source units from pages read during this question. The preferred roster read is an additional request, once per Genshin question. These checks cannot guarantee that a guide author or Qwen's selection is correct. When the bounded research cannot fill a gap, the answer identifies the missing core fields without treating snippets or headings as a finished build.

## Overlay, screenshots and voice

Drag the icon to move it; click it or press **Ctrl+Alt+R** to open chat. Resize chat from its **bottom-right grip**, and drag the heading to reposition it. Settings → Appearance changes icon image, size, opacity and hotkey. The tray menu still works at zero icon opacity. Borderless windowed games generally work better with this desktop overlay than exclusive fullscreen.

Screenshot mode sends the image only to the local vision model. Its game/quest observations go into the same search/read workflow. No image is sent to SearXNG. An optional YouTube API key adds video metadata cards; the app does not watch videos or fetch transcripts. Without that key, an optional YouTube search link remains available alongside the web answer.

The default system voice is a trial voice. No Raiden voice model or recording is included. For character speech, install [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) separately, load your speech model, and start its v2 API. Configure the local speech URL (usually `http://127.0.0.1:9880`), reference WAV, transcript and languages in Settings → Voice. The reference and speech model determine the voice; no RVC adapter is included. Existing speech playback, automatic reading, math rendering and icon controls are retained.

## Storage and privacy

**Raiden saves no guide pages, excerpts, search results or conversation history to disk.** Each question gets new search/page state. Relevant excerpts stay in chat RAM for citation previews until New chat or exit. Page text can be reused only within that single question; the next question starts fresh. New chat also clears the active subject. The model server may have independent logging settings.

The search service receives Qwen's search phrases and forwards them to its configured engines. It does not receive the screenshot, API key or full conversation. Public pages are fetched without browser cookies or credentials. The two allowed tools cannot run shell commands or write files. Private/local page destinations are rejected; only the configured SearXNG service uses loopback HTTP.

Settings remain in `%APPDATA%\RaidenCompanion\settings.json`, including keys, appearance, window size and service addresses. The app removes its legacy guide-cache files on launch. Speech uses a temporary WAV; unexpected shutdowns can leave that audio behind. Application errors may be written to error.log.

Docker stores the SearXNG software image and normal container metadata. The supplied configuration keeps its page-independent cache in RAM and disables Docker container logs. `search/.env` contains a generated service secret, not conversation data. Classic mode retains the old small address list and in-memory directory-link cache; neither is consulted by the new agent workflow.

## Validation and limits

All **183 regression tests** and the expanded offscreen interface check passed. They include the reported Hsin summary-table failure, accessible accordion labels, missing-field retries, close-spelling recovery, unknown equipment vocabulary, source-preview coverage, context compaction, navigation rejection, invented surnames/gear/effects, partial quotes, lost conditions, source rankings and ties, mixed roles, preferred-source fallback, wrong-character/game pages, follow-up context and unknown games. They also cover native tool-call transport over local HTTP, matching tool-call IDs, search/read/retry flow, source IDs, missing services, malformed tool requests, bounded requests, character/game context, unknown game terminology, real Trafilatura parsing, and settings migration. Interface checks cover the new tool flow, source previews and settings alongside resizing, math, hotkeys, images, cancellation and the retained classic retrieval.

Controlled tests use simulated model/search replies. Reader checks used live Hsin and Aemeath pages, plus Zenless Zone Zero, Neverness to Everness and Diablo 4 pages. Source-record selection was checked against these extracted pages. The existing Helper Team fixture checks also passed. These are reader/selection tests, not proof that the local model will make every research decision correctly: **a real Qwen/Bionic session and the SearXNG Docker container cannot be run in this workspace**. Check-Connections.bat performs that final integration check on your PC. Windows hotkeys, actual game capture/overlay behavior and your character voice also need a local check. Website changes, upstream search limits and small-model tool-use errors can still affect results.

Run checks after installing dependencies:

```
python -m unittest discover -s tests -v
python tests/ui_smoke.py
```

## Files and technical references

- `tool_agent.py`: model-directed tool loop and instructions for any game.
- `preferred_guides.py`: live Genshin Helper Team roster/build reader.
- `grounded_build.py`: complete-source-unit validation, completeness checks and preferred-source selection.
- `guide_records.py`: generic table/paragraph evidence records and navigation filtering.
- `guide_identity.py`: subject matching and cautious live-result spelling recovery.
- `web_tools.py`: local SearXNG JSON API, public-page reader and Trafilatura extraction.
- `core.py`: local model transport, routing, screenshots, speech and settings.
- `app.py` / `chat_format.py`: desktop overlay and formatted answers.
- `search/` / `search_setup.py`: local SearXNG container setup.
- `check_connections.py`: real integration check on your PC.
- `web_search.py` / `game_guides.py`: retained classic workflow plus shared page/context helpers.

The Helper Team site is publicly readable but its repository does not grant an open-source license. This app includes its own HTML reader, not their site code or guide collection; citations identify the live source.

Primary documentation used for the integration (checked 8 October 2026):

- [LM Studio tool calling](https://lmstudio.ai/docs/developer/openai-compat/tools)
- [SearXNG search API](https://docs.searxng.org/dev/search_api.html)
- [SearXNG container installation](https://docs.searxng.org/admin/installation-docker.html)
- [Trafilatura Python usage](https://trafilatura.readthedocs.io/en/latest/usage-python.html)

Unofficial personal fan project; not affiliated with HoYoverse. The icon is a static illustration, not Live2D animation.
