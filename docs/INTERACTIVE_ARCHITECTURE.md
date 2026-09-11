# Interactive AOS architecture

The existing execution path remains the system of record:

```text
cli.run
  -> ManagerAgent.create_plan
  -> DNAExtractor.extract_graph
  -> ConstraintPolicy + admission control
  -> GraphExecutor / SubAgent / CapabilityRegistry
  -> FailureManager
  -> IntegratorAgent
```

The interactive layer is an adapter around that path:

```text
Textual TUI -> CommandRegistry -> InteractiveService -> RequestContext -> cli.run
                     |                   |                  |
                     |                   |                  +-> Artifact references
                     |                   +-> ArtifactManager
                     +-> presentation only

cli.run / executor / recovery -> EventBus -> TUI + Session telemetry
```

`ArtifactManager` owns validation, MIME/category detection, durable session
copies and cleanup. It never plans or routes. The artifact category vocabulary
is `document`, `text`, `image`, `audio`, `video`, `dataset`, `archive`, and
`unknown`; the graph's older `modality` field is retained as an execution
compatibility signal.

Events are emitted only at observed lifecycle boundaries. Candidate routing
scores are forwarded from `CapabilityRegistry.select`; no score is synthesized
by the UI. Recovery events originate in `FailureManager`.

The current registered resources directly consume image and audio media. Other
artifact categories are retained in request/graph context, ready for future
registered parsers, rather than being coerced to text or silently processed.
