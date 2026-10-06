# Dev-Exam_practice

SSC exam practice web app built on 144,944 previous-year questions (CGL / CHSL / CPO / GD / MTS / Selection Post /
Stenographer, pre & mains, 2019–2026). It offers subject-wise practice, full mock tests on a replica of the real SSC
CBT interface, retrieval-augmented AI help, and a self-learning model of what you know that decides what you practise next.

The questions come from [akarohitmishra/repeatermock-subjectwise-db](https://github.com/akarohitmishra/repeatermock-subjectwise-db);
the importer clones it for you.

## Architecture

```
 145k PYQs ──► RAG search ──► LLM + trained models ──► Exam engine ◄──► Self-learning learner model
 (SQLite)      hybrid           Gemini (tutor, Ask AI,    papers, mocks,       updates on every answer,
               retrieval        coach, generator)         smart practice,      tunes itself, schedules
                                + topic model trained     personalised mocks   reviews, predicts score
                                  on the dataset
```

| Part | What it is | Where |
|---|---|---|
| **RAG search** | Every PYQ embedded locally with `minishlab/potion-retrieval-32M` (model2vec, about 27 s for the whole bank on CPU, no API quota), combined with SQLite FTS5 BM25 keyword ranking via Reciprocal Rank Fusion. Queries take 20–90 ms. Powers Search, "Similar questions", and the grounding of every Gemini answer. | `backend/app/rag.py` |
| **LLM** | Gemini with model fallback. Uses: tutor on every question (figures sent as images), **Ask AI** answers citing the retrieved PYQs, the mock coach, and a **question generator**. The generator retrieves real PYQs as examples, writes new questions, and keeps one only if an independent second solve agrees with its key; it also checks the topic model and rejects near-copies of PYQs. | `ai.py`, `generator.py` |
| **Fine-tuned model** | The Gemini API returns `501` for tuning on this key and there is no GPU, so the model fine-tuned here is a **topic classifier trained on the 130k labelled PYQs**: 98.2% subject accuracy and 83.3% chapter accuracy (96.4% top-3) on 14.5k held-out questions, trained in about 50 s. It classifies pasted questions, checks generated ones and audits mislabelled dataset rows. The bank is also exported as a supervised fine-tuning dataset for Vertex AI Gemini tuning or LoRA on open models. | `topic_model.py`, `finetune.py` |
| **Exam engine** | Real previous-year papers, random mocks, **Smart practice** (due reviews first, then topics ranked by exam weightage × how unsure the model is; every pick says why) and **personalised mocks** (the real pattern and timing, with chapters tilted to your weak ones). | `mocks.py`, `engine.py` |
| **Self-learning learner model** | An online 3-PL IRT model: `P(correct) = 0.25 + 0.75·σ(ability + subject skill + chapter skill − exam difficulty − question difficulty)`. Every answer updates it, with step sizes that shrink as evidence grows. Each forecast is stored **before** the answer is known, so calibration (Brier, log-loss vs a no-skill baseline) is measured honestly. Every 100 answers it replays your history under 9+ learning-rate settings and keeps the best. Missed questions go into a spaced-repetition queue. | `learner.py` |

## What you can do

- **Practise** by exam, stage, subject, one of 107 topics, year, or "got wrong last time" / "not attempted" / "bookmarked".
  You get the answer, the full solution and the model's forecast straight away. Keys `1`–`4` answer, `←`/`→` move.
- **Smart practice** (`/practice?mode=smart`): questions chosen by the adaptive engine, each with its reason.
- **Search** by meaning or keyword, or paste a whole question to find its twins and its topic. **Ask AI** answers from the
  retrieved PYQs, and the cited numbers jump to the questions.
- **Coach**: predicted score for your target exam, a mastery map of every topic, next steps, generated questions for your
  weakest topic, and how well the model knows you (a calibration plot plus a retrain button).
- **Mock tests**: any of 1,397 real papers, full random mocks or personalised mocks, in an exam room that behaves like the real CBT:
  - Save & Next / Mark for Review / Clear Response, with the five-state palette.
  - An answer only counts once saved.
  - Section timers where SSC uses them, and auto-submit when time runs out.
  - Survives a page refresh.
- **Results**: score with negative marking, section breakdown, time per question, full review, AI tutor, AI coach plan.
- **Progress**: accuracy by subject, weakest topics, score trend, daily activity. **AI Lab** (`/lab`) shows every model's status and metrics.

## Page URLs

Every page and state has a readable, shareable URL. Refresh, Back and Forward keep you on the same question, and older links redirect.

| URL | Page |
|---|---|
| `/practice`, `/practice/quant`, `/practice/quant/profit-and-loss` | practice: everything, a subject, a topic |
| `…?exam=ssc-cgl&stage=mains&year=2023&show=wrong&order=in-order&q=17264` | filters, plus the question on screen |
| `/practice/smart`, `/practice/ai` | smart practice, AI-generated questions |
| `/practice/paper/ssc-cgl-2023-07-18-shift-4` | one previous-year paper, untimed |
| `/question/17264`, `/practice/similar/17264` | one question, and questions like it |
| `/mocks/ssc-cgl`, `/mocks/ssc-cgl/mains` | mock tests for an exam and stage |
| `/mock/<id>`, `/mock/<id>/result` | exam room and result |
| `/search?q=red+fort`, `/coach`, `/progress`, `/ai-lab` | search, coach, progress, AI Lab |

Subjects use the slugs `reasoning`, `gk`, `quant`, `english` and `computer`. All URLs are built in `frontend/src/lib/urls.ts`.

## Exam patterns used

| Exam | Stage | Layout | Marking |
|---|---|---|---|
| CGL / CHSL | Tier-I | 100 Q (25 × Reasoning, GA, Quant, English), 60 min | +2 / −0.5 |
| CGL | Tier-II | Section I 60 min (Maths 30, Reasoning 30) · Section II 60 min (English 45, GA 25) · Section III 15 min (Computer 20) | +3 / −1 |
| CHSL | Tier-II | Section I 60 min (30 + 30) · Section II 60 min (English 40, GA 20) · Section III 15 min (Computer 15) | +3 / −1 |
| CPO | Paper-I / II | 200 Q, 120 min | +1 / −0.25 |
| GD | CBE | 80 Q, 60 min (old papers: 100 Q, 90 min) | +2 / −0.25 (old: +1 / −0.25) |
| MTS | CBT | Session-I 45 min (Maths 20, Reasoning 20, no negative) · Session-II 45 min (GA 25, English 25) | +3 / 0, then +3 / −1 |
| Selection Post | CBE | 100 Q, 60 min | +2 / −0.5 |
| Stenographer | CBT | 200 Q (Reasoning 50, GA 50, English 100), 120 min | +1 / −0.25 |

Previous-year papers keep their own per-question marks from the dataset. Patterns live in
[backend/app/catalog.py](backend/app/catalog.py) if SSC changes them.

## Deploy on Vercel

The repo deploys as one Vercel project with two [services](https://vercel.com/docs/services) (`vercel.json`):

| Service | Root | Public path | What it is |
|---|---|---|---|
| `frontend` | `frontend/` | everything except `/api/*` | the Vite React app, served from the CDN (deep links fall back to `index.html`) |
| `backend` | `backend/` | `/api/*` | the FastAPI app as one Python function |

There are no service bindings: the browser calls `/api` through the public route, and the backend never calls the frontend.

**Pre-trained artefacts, no training on deploy.** The data (about 325 MB unpacked) doesn't fit in the function bundle, which
Vercel caps at 225 MB, so it isn't bundled. Instead, at cold start the backend (`app/artifacts.py`) streams the four files of
this repo's **GitHub Release** listed in `backend/deploy/release.json` into `/tmp`, unpacking as they download and checking
every SHA-256:

- the question bank, with solutions zlib-compressed
- the int8 search vectors
- the trained topic model
- the MIT-licensed embedding model `minishlab/potion-retrieval-32M`

That adds a few seconds to a cold start on Vercel's network. Warm requests are unaffected. To publish new artefacts after
rebuilding locally:

```bash
cd backend
python deploy/package_release.py --tag data-v2      # writes ../release/* and deploy/release.json
gh release create data-v2 ../release/* --title "Question bank and models v2"
```

**Your progress** (answers, bookmarks, mocks, learner model, reviews, generated questions) is kept in a
[Turso](https://turso.tech) database, because Vercel's disk is temporary. The app copies the bank to `/tmp` at cold start
(about 3 s), pulls your progress, and pushes every change back. The app is **single-user**: anyone with the URL shares the
same progress and uses your Gemini key.

Vercel runs several instances at once and spreads one browser's requests across them. Every API response carries the
progress version it reflects (`X-Progress-Version`); the browser sends back the newest one it has seen, and an instance that
is behind pulls from Turso before answering. So a mock you just created opens on the first try, whichever instance gets
the request. **Without Turso** each instance keeps its own copy in `/tmp`: mocks show "mock not found" and progress comes
and goes between pages. `/api/lab` reports `"storage": "ephemeral"` in that case.

One-time setup in the Vercel dashboard:

1. **Add New → Project →** import `shivam-srivastava-031/Dev-Exam_practice`. Vercel reads `vercel.json`.
2. **Storage → Create Database → Turso** (Marketplace), pick the **Mumbai** region, and connect it to the project. This sets
   `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN`. Alternatively, create a database at turso.tech and add both variables yourself.
3. **Settings → Environment Variables:** add `GEMINI_API_KEY`.
4. **Settings → Functions → Region:** Mumbai (`bom1`), next to the database.
5. Deploy. Later pushes to `main` redeploy automatically.

## Local setup

Requires Python 3.11+, Node 20.19+ and git.

```bash
# 1. Backend
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt    # macOS/Linux: .venv/bin/pip; runtime-only deps: requirements.txt
cp .env.example .env                                  # then put your Gemini key in .env

# 2. Build everything: clone the dataset, import questions, build the RAG index, train the topic model (about 3 min)
.venv/Scripts/python -m app.importer

# 3. Frontend
cd ../frontend
npm install
npm run build

# 4. Run; the backend also serves the built frontend
cd ../backend
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

Open http://127.0.0.1:8000.

| Command (from `backend/`) | Does |
|---|---|
| `python -m app.importer --skip-ml` | Questions only, without the index or topic model |
| `python -m app.rag` | Rebuild the vector index |
| `python -m app.topic_model train` / `audit` | Retrain the classifier / list probable mislabels |
| `python -m app.finetune --max 20000` | Export the fine-tuning dataset to `data/finetune/` |
| `python -m pytest` | 45 tests: import, grading, practice, RAG, topic model, learner model, engine, generator, Turso sync, artifact download |
| `USE_BUNDLE=1 python -m uvicorn app.main:app` | Run exactly as deployed: downloads the release into temp storage on start |

**Development:** run the backend with `--reload` and `npm run dev` in `frontend/`. Vite serves http://localhost:5173 and
sends `/api` requests to the backend. Without a Gemini key the app still works; the AI features are hidden.

## Data notes

- 144,928 of 144,944 rows end up in the bank:
  - 13 rows have duplicate option labels (ambiguous answer); they are skipped.
  - 2 rows have no text; they are skipped.
  - 1 row repeats another question in the same paper and is merged with it.
- The importer strips decorative badge images, rewrites leftover LaTeX (`frac{sqrt{3}}{2}` → `(√3)/2`), normalises dates
  and moves one Stenographer paper that the source filed under GD.
- The topic-model audit flags about 45 more dataset rows labelled with the wrong subject (e.g. CHSL English synonym questions
  filed under Reasoning).
- AI-generated questions are stored with `origin = 'ai'`. They are practisable, but never enter real papers, mocks or the RAG index.
- Figures load from the dataset's CDN (`cdn.repeatermock.com`), so diagrams need an internet connection.
- Your progress, the learner model and reviews live in the same SQLite file and survive re-imports. Generated artefacts
  (`data/index`, `data/models`, `data/finetune`) are gitignored.

## Layout

```
backend/app/
  catalog.py      exam names, subjects, CBT patterns
  importer.py     dataset -> SQLite (cleaning, full-text index), then index + topic model
  rag.py          hybrid retrieval (model2vec vectors + FTS5 BM25, RRF)
  topic_model.py  classifier trained on the PYQs (train / predict / audit)
  learner.py      self-learning learner model, spaced repetition, calibration, self-tuning
  engine.py       smart practice and personalised mocks
  mocks.py        real papers, random mocks, SSC-style grading
  ai.py           Gemini: tutor, Ask AI (RAG), coach, structured generation, model fallback
  generator.py    grounded, self-verified question generation
  finetune.py     supervised fine-tuning dataset export
  main.py         FastAPI routes; also serves frontend/dist
frontend/src/
  pages/          Dashboard, Practice, Mocks, ExamRoom, Result, Search, Coach, Lab, Analytics
  components/     rich text, options, AI tutor, charts (incl. calibration plot)
```
