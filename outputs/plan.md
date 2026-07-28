# Task Plan
**Job:** define co-po mapping with wk mapping

| # | Node ID | Description | Capability | Depends On |
|---|---------|-------------|------------|------------|
| a | a | Search for definitions of co-po mapping and wk mapping | web_search | - |
| b | b | Summarize the search results for co-po mapping | summarization | a |
| c | c | Summarize the search results for wk mapping | summarization | a |
| d | d | Compare and contrast co-po mapping and wk mapping based on the summaries | summarization | b, c |

**Waves:** Wave 0: a | Wave 1: b, c | Wave 2: d

```mermaid
flowchart TD
    subgraph Wave 0
        a["Search for definitions of co-po mappi... (web_search)"]
    end
    subgraph Wave 1
        b["Summarize the search results for co-p... (summarization)"]
        c["Summarize the search results for wk m... (summarization)"]
    end
    subgraph Wave 2
        d["Compare and contrast co-po mapping an... (summarization)"]
    end
    a --> b
    a --> c
    b --> d
    c --> d

    classDef web_searchStyle fill:#4CAF50,color:#fff
    classDef summarizationStyle fill:#2196F3,color:#fff
    classDef visionStyle fill:#FF9800,color:#fff
    class a web_searchStyle
    class b summarizationStyle
    class c summarizationStyle
    class d summarizationStyle
```
