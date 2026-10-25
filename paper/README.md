# MedicalAOS paper package

This folder contains a six-page IEEEtran/A4 draft focused on the implemented
system architecture and operational evaluation.

## Files

- `medicalaos_paper.tex`: manuscript source.
- `refs.bib`: bibliography.
- `figure_prompt.md`: detailed prompt for generating the architecture figure.
- `quality_audit.md`: research-integrity and LaTeX audit.
- `medicalaos_paper.pdf`: compiled paper after validation.

## Compile

```bash
latexmk -pdf -interaction=nonstopmode -halt-on-error medicalaos_paper.tex
```

The manuscript intentionally does not claim diagnostic accuracy. Its evaluation
reports the actual offline regression result and historical saved-run behavior,
including fixture and coverage limitations.
