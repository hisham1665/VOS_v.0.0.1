# Task Plan
**Job:** FIFA World Cup analysis

| # | Node ID | Description | Capability | Depends On |
|---|---------|-------------|------------|------------|
| a | a | Describe the image and identify the team | vision | - |
| b | b | Search for Brazil FIFA World Cup stats | web_search | - |
| c | c | Search for Portugal FIFA World Cup stats | web_search | - |
| d | d | Search for Spain FIFA World Cup stats | web_search | - |
| e | e | Search for France FIFA World Cup stats | web_search | - |
| f | f | Search for England FIFA World Cup stats | web_search | - |
| g | g | Summarize Brazil FIFA World Cup stats | summarization | b |
| h | h | Summarize Portugal FIFA World Cup stats | summarization | c |
| i | i | Summarize Spain FIFA World Cup stats | summarization | d |
| j | j | Summarize France FIFA World Cup stats | summarization | e |
| k | k | Summarize England FIFA World Cup stats | summarization | f |
| l | l | Compare and summarize all teams stats | summarization | g, h, i, j, k |
| m | m | Search for the team in the image and its stats | web_search | a |
| n | n | Summarize the team in the image stats | summarization | m |
| o | o | Search for the current FIFA World Cup champion | web_search | - |
| p | p | Summarize the current FIFA World Cup champion | summarization | o |
| q | q | Compare the team in the image position with other teams | summarization | l, n |

**Waves:** Wave 0: a, b, c, d, e, f, o | Wave 1: m, g, h, i, j, k, p | Wave 2: n, l | Wave 3: q

```mermaid
flowchart TD
    subgraph Wave 0
        a["Describe the image and identify the team (vision)"]
        b["Search for Brazil FIFA World Cup stats (web_search)"]
        c["Search for Portugal FIFA World Cup stats (web_search)"]
        d["Search for Spain FIFA World Cup stats (web_search)"]
        e["Search for France FIFA World Cup stats (web_search)"]
        f["Search for England FIFA World Cup stats (web_search)"]
        o["Search for the current FIFA World Cup... (web_search)"]
    end
    subgraph Wave 1
        m["Search for the team in the image and ... (web_search)"]
        g["Summarize Brazil FIFA World Cup stats (summarization)"]
        h["Summarize Portugal FIFA World Cup stats (summarization)"]
        i["Summarize Spain FIFA World Cup stats (summarization)"]
        j["Summarize France FIFA World Cup stats (summarization)"]
        k["Summarize England FIFA World Cup stats (summarization)"]
        p["Summarize the current FIFA World Cup ... (summarization)"]
    end
    subgraph Wave 2
        n["Summarize the team in the image stats (summarization)"]
        l["Compare and summarize all teams stats (summarization)"]
    end
    subgraph Wave 3
        q["Compare the team in the image positio... (summarization)"]
    end
    b --> g
    c --> h
    d --> i
    e --> j
    f --> k
    g --> l
    h --> l
    i --> l
    j --> l
    k --> l
    a --> m
    m --> n
    o --> p
    l --> q
    n --> q

    classDef web_searchStyle fill:#4CAF50,color:#fff
    classDef summarizationStyle fill:#2196F3,color:#fff
    classDef visionStyle fill:#FF9800,color:#fff
    class a visionStyle
    class b web_searchStyle
    class c web_searchStyle
    class d web_searchStyle
    class e web_searchStyle
    class f web_searchStyle
    class g summarizationStyle
    class h summarizationStyle
    class i summarizationStyle
    class j summarizationStyle
    class k summarizationStyle
    class l summarizationStyle
    class m web_searchStyle
    class n summarizationStyle
    class o web_searchStyle
    class p summarizationStyle
    class q summarizationStyle
```
