# System-level design diagrams

This document is a **visual companion** to `sdlc-deep-agent-spec.md` and `ARCHITECTURE.md`. Diagrams use [Mermaid](https://mermaid.js.org/); they render in GitHub, GitLab, many IDEs, and Cursor preview.

---

## 1. System context (C4-style)

Who talks to what at the boundary of the **target repository** and the **agent process**.

```mermaid
flowchart TB
  subgraph Actors
    OP[Operator / integrator]
    HM[Human approver optional HITL]
  end

  subgraph External["External systems"]
    OAI[OpenAI Chat Completions API]
    GIT[Git working copy]
    JIT[Jira issue source fixture JSON or future MCP HTTP]
  end

  subgraph Process["sdlc_agent process"]
    direction TB
    ORCH["Orchestrator<br/>dispatcher + FSM + gate routing"]
    CG["CurationGate"]
    GA["GateApprover"]
    LOAD["SkillLoader<br/>skills *.md"]

    subgraph Subagents["Subagents recursion depth 1"]
      BA[BacklogAnalyzer]
      DV[Developer]
      PR[PRReviewer]
    end

    REG["Subagent registry<br/>dict role to instance"]

    ORCH --> REG
    ORCH --> CG
    ORCH --> GA
    REG --> BA
    REG --> DV
    REG --> PR
    LOAD -. inject system prompt slice .-> BA
    LOAD -. inject system prompt slice .-> DV
    LOAD -. inject system prompt slice .-> PR
  end

  subgraph TargetRepo["Target project repo disk"]
    DA[".deepagent"]
    subgraph DAinner[" "]
      direction LR
      ST[state]
      AR[artifacts]
      PM[project_memory + lore]
      EP[episodic JSONL]
      TR[trajectories session task JSONL]
    end
  end

  OP --> ORCH
  HM -. GateApprover .-> GA

  BA --> OAI
  BA --> JIT
  DV --> OAI
  PR --> OAI
  PR --> GIT
  DV --> SBX["LocalSubprocessSandbox<br/>scoped cwd + tests"]

  ORCH <--> DA
  CG <--> DA
  BA -. no direct .deepagent read write .-> DA
  DV -. no direct .deepagent read write .-> DA
  PR -. no direct .deepagent read write .-> DA
```

**Legend:** Solid arrows are runtime dependencies (calls, reads, writes). Dotted lines are optional injection or human-in-the-loop. Only the orchestrator path (through `MemoryStores` and trajectory paths) persists to `.deepagent/`; subagents receive **assignment payloads** only.

---

## 2. Internal components and data ownership

Logical modules inside `src/sdlc_agent/` and ownership of persistence.

```mermaid
flowchart LR
  subgraph Contracts
    TA["TaskAssignment"]
    AR["ArtifactReturn<br/>verification proposed_memory"]
  end

  subgraph Orchestrator_pkg["orchestrator/"]
    SM["state_machine.py<br/>pure FSM transitions"]
    DP["dispatcher.py<br/>Orchestrator"]
    CU["curation.py"]
    HI["hitl.py"]
  end

  subgraph Memory_pkg["memory/"]
    PTH["paths.py DeepAgentPaths"]
    STO["stores.py MemoryStores"]
    TRK["trajectories.py TrajectoryRecorder"]
    INI["initializer.py"]
  end

  subgraph Integration
    LLM["llm OpenAIClient"]
    MCP["mcp jira git"]
    SBX["sandbox"]
  end

  subgraph Workers["subagents/"]
    BA2[backlog_analyzer]
    DV2[developer]
    PR2[pr_reviewer]
  end

  DP --> SM
  DP --> CU
  DP --> HI
  DP --> STO
  DP --> TA
  BA2 --> TA
  DV2 --> TA
  PR2 --> TA
  BA2 --> AR
  DV2 --> AR
  PR2 --> AR

  STO --> PTH
  TRK --> PTH
  INI --> PTH

  BA2 --> LLM
  DV2 --> LLM
  PR2 --> LLM
  BA2 --> MCP
  PR2 --> MCP
  DV2 --> SBX
```

**Import note:** `Orchestrator` is imported from `sdlc_agent.orchestrator.dispatcher` (not from `orchestrator.__init__`) to avoid a circular import with `memory.stores`.

---

## 3. Memory layout (single target repo)

Three logical stores plus cold trajectories, as on disk.

```mermaid
flowchart TB
  subgraph Working["1 Working state per ticket"]
    SF["state ticket_id.json<br/>TicketState current_phase attempts history"]
  end

  subgraph Curated["2 Curated cross-session"]
    PJ["project_memory.json facts"]
    LO["subagent_lore role.json lore entries"]
  end

  subgraph Audit["3 Episodic audit append only"]
    EP["episodic log.jsonl<br/>kind session_id ticket_id transitions"]
  end

  subgraph Art["Per-ticket artifacts orchestrator writes"]
    A1["requirement_analysis.json"]
    A2["implementation_summary.json"]
    A3["review.json"]
  end

  subgraph Cold["Cold storage no auto reload into context"]
    TJ["trajectories session_id task_id.jsonl<br/>LLM prompt response per line"]
  end

  ORC[Orchestrator CurationGate] --> Working
  ORC --> Curated
  ORC --> Audit
  ORC --> Art
  SUB[Subagents] -. propose only .-> ORC
  TR[TrajectoryRecorder when wired] --> Cold
```

---

## 4. SDLC state machine (phases)

High-level FSM; gate **decisions** are `proceed`, `retry`, `blocked`, `needs_human` (see `state_machine.py`).

```mermaid
stateDiagram-v2
  [*] --> INTAKE
  INTAKE --> REQUIREMENTS_ANALYSIS : intake
  REQUIREMENTS_ANALYSIS --> REQUIREMENTS_GATE : dispatch BacklogAnalyzer
  REQUIREMENTS_GATE --> DEVELOPMENT : proceed
  REQUIREMENTS_GATE --> REQUIREMENTS_ANALYSIS : retry
  REQUIREMENTS_GATE --> BLOCKED : blocked
  REQUIREMENTS_GATE --> NEEDS_HUMAN : needs_human

  DEVELOPMENT --> DEVELOPMENT_GATE : dispatch Developer
  DEVELOPMENT_GATE --> PR_REVIEW : proceed
  DEVELOPMENT_GATE --> DEVELOPMENT : retry
  DEVELOPMENT_GATE --> BLOCKED : blocked
  DEVELOPMENT_GATE --> NEEDS_HUMAN : needs_human

  PR_REVIEW --> REVIEW_GATE : dispatch PRReviewer
  REVIEW_GATE --> DONE : proceed
  REVIEW_GATE --> PR_REVIEW : retry
  REVIEW_GATE --> BLOCKED : blocked
  REVIEW_GATE --> NEEDS_HUMAN : needs_human

  DONE --> [*]
  BLOCKED --> [*]
  NEEDS_HUMAN --> [*]
```

---

## 5. Single work-phase sequence (dispatch to curation)

Typical flow for one subagent invocation; Developer adds multiple LLM steps inside one `run()`.

```mermaid
sequenceDiagram
  autonumber
  participant O as Orchestrator
  participant M as MemoryStores
  participant S as Subagent
  participant L as OpenAI API
  participant T as TrajectoryRecorder optional
  participant C as CurationGate

  O->>M: load TicketState
  O->>O: build TaskAssignment injected context prior inputs
  O->>S: run assignment
  loop each LLM call when using structured output
    S->>L: chat completion JSON schema
    L-->>S: assistant text JSON
    opt recorder wired
      S->>T: append prompt response kind
    end
  end
  S-->>O: ArtifactReturn
  O->>M: save phase artifact JSON
  O->>M: append episodic dispatch
  O->>C: evaluate proposed_memory
  C->>M: promote or reject log proposal_received promotion rejection
  O->>O: transition to gate phase
  O->>M: save TicketState
```

---

## 6. Least privilege: what each role touches

```mermaid
flowchart TB
  subgraph Orchestrator_only["Orchestrator only"]
    W["write .deepagent all stores"]
    R["read all stores build assignments"]
  end

  subgraph BA["BacklogAnalyzer"]
    B1["read Jira fixture MCP"]
    B2["call LLM"]
    B3["no filesystem to target repo except via OS process"]
  end

  subgraph DV["Developer"]
    D1["sandbox root only read write"]
    D2["call LLM"]
    D3["run tests subprocess in sandbox"]
  end

  subgraph PRr["PRReviewer"]
    P1["git read diff files"]
    P2["call LLM"]
    P3["no sandbox write"]
  end
```

---

## Related reading

| Document | Use when |
|----------|----------|
| `sdlc-deep-agent-spec.md` | Full contracts, gates, build phases |
| `ARCHITECTURE.md` | Tradeoffs and extension seams |
| `README.md` | Setup, demo, test commands |
