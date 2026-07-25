# Research basis and comparison

Research cut-off: 2026-07-25.

This document compares the current checkout with primary papers and official
technical specifications. It does not treat design proposals as implemented
features, and it does not infer standards compliance from conceptual
similarity.

The defensible public position is:

> **TIMECODE-AGENT is a local-first, evidence-traceable workflow for
> progressive video inspection, reusable timestamped understanding, and
> OTIO-based editorial handoff.**

The current differentiation is durable local artifacts, resumable inspection,
explicit evidence and decision history, and editor handoff. It is not a claim
of being first, unique, state of the art, or equivalent to a provenance
standard.

## Current architecture against research concepts

| Research concept | Current implementation | Boundary |
|---|---|---|
| active visual inspection | the skill forms transcript hypotheses, then the agent selects candidate timestamps from signals and gaps | agent-driven policy; there is no autonomous semantic selector inside the Python runtime |
| placement versus selection | `keyframes`, scenes, highlights and learned adapters propose candidate locations; the agent decides meaning | P1 model output is a cue, not deterministic semantic truth |
| persistent memory | manifest, transcript, append-only checkpoint history and corpus views survive a session | user-owned local artifacts, not a learned hidden-state memory |
| evidence provenance | capture cause/time relations and resolvable transcript or image support are stored | no cryptographic integrity and no automatic semantic entailment proof |
| compile once, query many | search and wiki reuse persisted artifacts before targeted reinspection | no guarantee that every future question can avoid the source video |
| observer/editor separation | separate checkpoint and sequence ledgers with one-way write semantics | one current policy skill; no typed hand-back or separate edit agent |
| cognitive editing | word-boundary, breathing-room, loudness and grounding checks | viewer attention, memory, affect and surprise are not estimated |

## Long-video understanding comparators

| Work | Primary-source status | Relevant mechanism | Difference from this repository |
|---|---|---|---|
| [VideoAgent](https://eccv.ecva.net/virtual/2024/poster/1090) | ECCV 2024 | iterative frame retrieval with answer reflection and continued search | task-time QA loop; no demonstrated user-owned cross-session ledger |
| [DrVideo](https://openaccess.thecvf.com/content/CVPR2025/html/Ma_DrVideo_Document_Retrieval_Based_Long_Video_Understanding_CVPR_2025_paper.html) | CVPR 2025 | coarse textual document, key-frame retrieval and iterative document augmentation | document is inference state rather than a persistent corpus contract |
| [ReWind](https://openaccess.thecvf.com/content/CVPR2025/html/Diko_ReWind_Understanding_Long_Videos_with_Instructed_Learnable_Memory_CVPR_2025_paper.html) | CVPR 2025 | instruction-relevant learned memory plus detailed frame selection | learned model memory, not an inspectable append-only user artifact |
| [LongVideoAgent](https://aclanthology.org/2026.acl-long.1876/) | ACL 2026 | subtitle-grounded coordination with temporal grounding and targeted visual observation | closest policy pattern, but evaluated as QA rather than durable editorial knowledge |
| [VideoARM](https://openaccess.thecvf.com/content/CVPR2026/html/Yin_VideoARM_Agentic_Reasoning_over_Hierarchical_Memory_for_Long-Form_Video_Understanding_CVPR_2026_paper.html) | CVPR 2026 | adaptive observe-think-act-memorize loop with hierarchical multimodal memory | no public cross-session application ledger contract |
| [LongVideo-R1](https://openaccess.thecvf.com/content/CVPR2026/html/Qiu_LongVideo-R1_Smart_Navigation_for_Low-cost_Long_Video_Understanding_CVPR_2026_paper.html) | CVPR 2026 | learned hierarchical navigation and sufficiency-based early stopping | learned navigation rather than explicit evidence history |
| [HAVEN](https://openaccess.thecvf.com/content/CVPR2026/html/Yin_Hierarchical_Long_Video_Understanding_with_Audiovisual_Entity_Cohesion_and_Agentic_CVPR_2026_paper.html) | CVPR 2026 | global/scene/segment/entity audiovisual indexing with agentic search | stronger entity cohesion; different persistence and handoff objective |
| [WorldMM](https://openaccess.thecvf.com/content/CVPR2026/html/Yeo_WorldMM_Dynamic_Multimodal_Memory_Agent_for_Long_Video_Reasoning_CVPR_2026_paper.html) | CVPR 2026 | episodic, semantic and visual memories with adaptive retrieval | evaluated primarily for reasoning accuracy, not local editorial provenance |

In this surveyed set, the word *memory* does not by itself establish durable,
cross-session, user-owned storage. That property must be verified at the
artifact and lifecycle level.

## Frame selection and benchmark context

- [Adaptive Keyframe Sampling](https://openaccess.thecvf.com/content/CVPR2025/html/Tang_Adaptive_Keyframe_Sampling_for_Long_Video_Understanding_CVPR_2025_paper.html)
  (CVPR 2025) balances prompt relevance and video coverage under a frame
  budget. Its official paper should not be described as a
  facility-location/submodular method.
- [Adaptive Greedy Frame Selection](https://arxiv.org/abs/2603.20180) is a
  separate 2026 arXiv preprint that uses modular relevance plus
  facility-location coverage and gives the corresponding greedy guarantee.
- [Divide then Ground](https://openaccess.thecvf.com/content/CVPR2026/html/Li_Divide_then_Ground_Adapting_Frame_Selection_to_Query_Types_for_CVPR_2026_paper.html)
  (CVPR 2026) supports the general idea that frame policy should depend on
  query type. The current runtime does not implement its router.
- [VRBench](https://vrbench.github.io/) (ICCV 2025),
  [NA-VQA](https://openaccess.thecvf.com/content/CVPR2026W/CV4Smalls/html/Jain_Narrative_Aligned_Long_Form_Video_Question_Answering_CVPRW_2026_paper.html)
  (CVPR 2026 workshop), and
  [SeriesBench](https://openaccess.thecvf.com/content/CVPR2025/html/Zhang_SeriesBench_A_Benchmark_for_Narrative-Driven_Drama_Series_Understanding_CVPR_2025_paper.html)
  (CVPR 2025) are evaluation datasets, not competing persistent-memory
  products.

The public benchmark in this repository is a regression gate for ingest,
signal counts, recommended mode, readiness state and a generous runtime
ceiling. It is not directly comparable to these QA benchmarks and does not
establish answer accuracy or selection optimality.

## Computational editing context

| Work | Status | Relevant scope |
|---|---|---|
| [Computational Video Editing for Dialogue-Driven Scenes](https://graphics.stanford.edu/papers/roughcut/) | ACM TOG / SIGGRAPH 2017 | aligns scripts and takes, then applies dialogue-editing idioms with HMMs |
| [Watch to Edit](https://arxiv.org/abs/1807.03125) | Eurographics 2018 | gaze-driven cuts and retargeting through optimization |
| [EditIQ](https://doi.org/10.1145/3708359.3712113) | ACM IUI 2025 | optimized shot selection for stationary wide-angle, dialogue-driven scenes |
| [EditDuet](https://arxiv.org/abs/2509.10761) | arXiv preprint, 2025 | editor-critic collaboration for nonlinear editing |
| [Crayotter](https://arxiv.org/abs/2606.07636) | arXiv preprint, 2026 | traceable long-form editing artifacts, tool calls and resumable workflow |
| [CutClaw](https://arxiv.org/abs/2603.29664) | arXiv preprint, 2026 | playwriter/editor/reviewer pipeline for music-synchronized short videos |

The current editing surface is narrower than a viewer-state editor. It stores
ordered trims and rejected alternatives, evaluates deterministic boundary
features, pins terminal cuts to checkpoint revisions, and exports editable
timeline data. Aesthetic scoring remains agent or human judgment.

## Standards and interchange boundary

- [OpenTimelineIO](https://opentimelineio.readthedocs.io/en/latest/index.html)
  is an active ASWF API and interchange-format project for editorial cut
  information. TIMECODE-AGENT exports OTIO timeline data; this is not a
  universal NLE compatibility guarantee, and media remains externally
  referenced.
- [W3C PROV-O](https://www.w3.org/TR/prov-o/) defines interoperable provenance
  concepts such as Entity, Activity, Agent, generation, use and derivation.
  The local ledger is conceptually mappable to those ideas but does not
  implement a PROV serialization or conformance claim.
- [C2PA 2.4](https://spec.c2pa.org/specifications/specifications/2.4/specs/C2PA_Specification.html)
  binds signed claims to media assets and supports tamper-evident validation.
  An append-only JSONL application ledger is not C2PA-equivalent without
  manifests, signatures, content binding and validation.

## Research ideas intentionally not presented as shipped

- autonomous query-routed evidence selection;
- edit-specific value-of-information and typed reinspection requests;
- viewer attention, working-memory, emotion or Bayesian-surprise estimation;
- learned multi-video identity resolution;
- expert preference or human revision-cost optimization;
- cryptographic provenance or standards conformance;
- guaranteed zero-rewatch operation;
- a universal compile-once cost break-even.

These remain candidates for independently scored experiments. They should not
be promoted into public feature claims merely because the architecture can
represent adjacent data.
