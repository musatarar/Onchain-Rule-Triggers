## Writing explanations

Write for an engineer who has to build or debug what you describe, not for a
manager reading a report.

- Answer only what was asked. A question like "how does X work" or "what is Y
  for" gets a short description and one example. Don't add background, file
  lists, test plans or other sections nobody asked for. The reader will ask if
  they want more. If you find a real problem they need to know about, add it
  at the end in one or two sentences.
- Explain what the reader doesn't know yet with an example. For a table or
  field, show a row with real values in the columns that matter, and say when
  it is written and what reads it. A name in parentheses is not an
  explanation.
- When you explain a design, or why a field exists, name the obvious
  alternative and show a concrete case where it gives a wrong result. Do this
  even if the reader didn't ask "why".
- Write plain sentences. Don't open with "Short answer:", "Bottom line:" or
  "TL;DR". Don't write headline fragments such as "Why the cache lives on the
  session". Don't use "X, not Y" lines to sound decisive. Headings and bold
  labels only name the topic ("Storage", "When rows are written").
- Keep separate facts in separate sentences. "The build passes and the only
  failures are the 3 snapshot tests" should be "The build passes. The only
  failures are the 3 snapshot tests." When one fact causes another, say so with
  "because" or "so".
- State what a fact means for the reader. "Only paid plans are invoiced" leaves
  them to work out the rest. "Free accounts get no invoice, so their invoice_id
  is null" doesn't.
