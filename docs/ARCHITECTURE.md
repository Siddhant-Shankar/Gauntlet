# Architecture

How Gauntlet is put together, in six diagrams. GitHub draws them from the Mermaid text below.

1. [Who talks to whom](#who-talks-to-whom)
2. [The models, in the order a run uses them](#the-models-in-the-order-a-run-uses-them)
3. [One run: a workflow per case](#one-run-a-workflow-per-case)
4. [One attempt, step by step](#one-attempt-step-by-step)
5. [A worker crashes mid-run](#a-worker-crashes-mid-run)
6. [Threads inside a worker](#threads-inside-a-worker)

## Who talks to whom

The web app and the CLI only talk to Temporal: they start runs, read them and send reviews. Only the
worker runs Gauntlet's code, and only its activities touch Claude or the database. Restarting the web
app loses nothing, because the Temporal server keeps every run.

```mermaid
flowchart LR
    subgraph clients["Clients: they never run the eval"]
        browser["Browser<br/>start, run and compare pages"]
        web["Web app<br/>FastAPI and runs.py"]
        cli["CLI<br/>gauntlet run"]
    end
    temporal["Temporal server<br/>every run's history<br/>the 'gauntlet' task queue<br/>timeouts, retries, timers"]
    saved[("temporal.db<br/>saved runs")]
    subgraph worker["Worker process"]
        wf["Workflow code<br/>RunWorkflow, CaseWorkflow"]
        act["Activities<br/>load_schema, ask_agent, check_answer"]
    end
    claude["Claude API"]
    shop[("shop.db<br/>SQLite, read-only")]

    browser -- "HTTP JSON, polls every 2 s" --> web
    web -- "start, query, update, read history" --> temporal
    cli -- "start, wait for the result" --> temporal
    temporal --- saved
    worker -- "long-polls for tasks, sends back results" --> temporal
    act -- "one call per attempt, 30 s timeout" --> claude
    act -- "both queries, read-only" --> shop
```

## The models, in the order a run uses them

Every model is in `src/gauntlet/models.py` and is frozen. A box inside another is one of its fields.
Each arrow is labelled with the code that does the conversion.

```mermaid
flowchart TB
    subgraph sDefine["Step 1: write the suite"]
        direction LR
        mSuite["Suite"] --> mCase["Case<br/>id, prompt"] --> mSql["SqlResult<br/>the answer key"]
    end
    subgraph sStart["Step 2: start a run"]
        direction LR
        mReq["StartRequest<br/>sent by the page"] -- "one per agent" --> mCfg["RunConfig<br/>k, db_path, agent"]
    end
    subgraph sRun["Step 3: run each attempt"]
        direction LR
        mAtt["Attempt<br/>number, reply"] --> mOut["Outcome<br/>Passed, Failed, Errored,<br/>NeedsReview or Reviewed"]
    end
    subgraph sReview["Step 4: a person decides"]
        mVer["Verdict<br/>attempt, passed, note"]
    end
    subgraph sRead["Step 5: read and score"]
        direction LR
        mRun["RunView"] --> mCv["CaseView"] --> mCs["CaseScore<br/>pass^k and pass@k"]
        mSs["SuiteScore<br/>for the CLI report"]
    end
    mCase -- "the case" --> mAtt
    mCfg -- "the config" --> mAtt
    mVer -- "review update: becomes Reviewed" --> mOut
    mAtt -- "load_run" --> mCv
    mAtt -- "score_suite" --> mSs
```

## One run: a workflow per case

`RunWorkflow` starts one `CaseWorkflow` per question, all at once. Each child's id is the run id, a
slash and the case id, which is how the run page queries a case and how a review reaches it. Steps that
touch the outside world are activities, and the server saves each one's result in the case's history.

```mermaid
flowchart TD
    subgraph caseWf["CaseWorkflow: run id / count_customers"]
        direction TB
        ls["activity: load_schema"] --> attempts{"attempt 1 to k"}
        attempts --> ask["activity: ask_agent<br/>heartbeats every 2 s"]
        ask -- "reply saved in the history" --> chk["activity: check_answer<br/>check_sql on the reply"]
        chk -- "Outcome saved" --> attempts
        attempts -- "all k done" --> pending{"any attempt<br/>NeedsReview?"}
        pending -- "yes" --> timer["wait for a person<br/>durable 7-day timer"]
        pending -- "no" --> done["return the attempts"]
        timer --> done
    end
    begin(["start_workflow: RunWorkflow with the suite and config"]) --> runWf
    runWf["RunWorkflow<br/>id: suite, agent and start time"]
    runWf -- "one child per case, all at once" --> caseWf
    runWf --> other["CaseWorkflow: run id / top_3_products<br/>and one more per case"]
    page["Run page"] -. "query attempts, every 2 s" .-> caseWf
    page -. "update review(verdict)" .-> caseWf
```

## One attempt, step by step

Asking and checking are two separate activities, so the agent's reply is saved before anything
judges it. If the check fails and is retried, the agent isn't asked again.

```mermaid
sequenceDiagram
    autonumber
    participant S as Temporal server
    participant W as CaseWorkflow
    participant A as Activity thread
    participant H as Helper thread
    participant C as Claude API
    participant D as shop.db
    W->>S: schedule ask_agent with the agent name, prompt and schema
    S->>A: activity task
    A->>H: pool.submit(agent.answer)
    H->>C: one Messages API call
    loop every 2 s until the reply arrives
        A->>S: heartbeat
    end
    C-->>H: reply text
    H-->>A: reply
    A->>S: completed: the reply is saved in the history
    S->>W: wake up with the reply
    W->>S: schedule check_answer with the case, reply and db_path
    S->>A: activity task
    A->>D: reference SQL, then the agent's SQL, read-only
    A->>S: completed: the Outcome is saved in the history
    S->>W: wake up with the Outcome
    Note over W: append Attempt(number, reply, outcome) and start the next attempt
```

## A worker crashes mid-run

Recorded on 2026-10-20 with a slow fake agent and k = 3. Worker A was killed during attempt 2's agent
call, and worker B finished the case. `tests/test_worker.py` checks the same thing with a real process
kill.

```mermaid
sequenceDiagram
    participant A as Worker A
    participant S as Temporal server
    participant B as Worker B
    Note over A,S: 0 to 0.55 s: load_schema and attempt 1, all results saved
    A->>S: heartbeat while attempt 2's agent call runs
    Note over A: 0.73 s: process killed
    Note over S: no heartbeat for 6 s, so that try times out
    Note over S: then a 1 s retry backoff
    S->>B: 8.03 s: ask_agent for attempt 2, try 2
    B->>S: 8.55 s: reply saved
    Note over S: the next workflow task waits 10 s on worker A's own queue
    S->>B: 18.55 s: workflow task, from the shared queue
    Note over B: replay from the top: the schema, attempt 1 and the new reply come from the history
    B->>S: attempt 2's check, then attempt 3
    Note over B,S: 19.12 s: the case is done, and attempt 1 was never asked again
```

## Threads inside a worker

A workflow that's waiting isn't on any thread: it's history on the server. Workflow code runs for a few
milliseconds at a time. Activities are plain functions, so each one holds an activity thread while it
runs, and `ask_agent` runs the Claude call on its own helper thread so the activity thread can keep
heartbeating.

```mermaid
flowchart TB
    subgraph server["Temporal server"]
        queue["task queue 'gauntlet'<br/>workflow tasks and activity tasks"]
    end
    subgraph worker["Worker process"]
        core["SDK core, in Rust<br/>gRPC, replay, the sticky cache"]
        main["MainThread<br/>asyncio event loop"]
        wft["Workflow threads, the SDK's own pool<br/>a few milliseconds per step"]
        actt["Activity threads: ThreadPoolExecutor()<br/>min(32, CPUs + 4) threads"]
        helper["Helper thread, one per ask_agent<br/>makes the Claude call"]
    end
    queue -- "tasks" --> core
    core -- "results, heartbeats, commands" --> queue
    core --> main
    main -- "workflow task" --> wft
    main -- "activity task" --> actt
    actt -- "pool.submit" --> helper
```

**A known limit.** The worker doesn't cap how many activities it accepts at once. If more arrive than
there are activity threads, the extra ones wait for a thread without heartbeating, so the server times
them out and retries them. In a recorded test with 4 cases, 2 threads and a 7 s agent, that made 8 agent
calls instead of 4. Setting the worker's `max_concurrent_activities` to the thread pool's size would fix it.
