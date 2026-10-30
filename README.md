# judge-bas-review

Judge Accountants — BAS Review Memo PDF renderer.

`bas_memo_renderer.py` turns the BAS review agent's JSON memo into the branded A4 PDF
(fillable review checkboxes, locked indicator rows, GST Variance Explanation and Notes to Reviewer
text fields, green Xero links, blue section jumps, red High rows) and verifies every link with pypdf.

```
pip install reportlab pypdf
python3 bas_memo_renderer.py bas_memo_sample.json out.pdf [--logo logo.png] [--font-dir DIR]
```

Exit code 0 = rendered and verified. The JSON contract is documented in the Project file
`claude_BAS-Memo-Renderer-Procedure.md`. `bas_memo_sample.json` is an anonymised worked example.

Agents fetch a pinned version, e.g.
`https://raw.githubusercontent.com/barclay-j/judge-bas-review/v1.0/bas_memo_renderer.py`.
Change the script here and bump the tag; never patch it inside a run.
