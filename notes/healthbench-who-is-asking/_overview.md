```{=html}
<svg class="diagram" role="img" viewBox="0 0 360 400" aria-labelledby="overview-title overview-desc" xmlns="http://www.w3.org/2000/svg" font-family="inherit">
<title id="overview-title">How this experiment works</title>
<desc id="overview-desc">A HealthBench conversation, where a good answer depends on who is asking, goes to two models. Claude Opus 5.5 answers through Anthropic's API; Gemma 3 12B answers on a laptop. GPT-4.1, the judge, checks each answer against the checklist physicians wrote for that conversation, marking each item met or not met. Each answer's score is the points it earned out of the points it could have earned, and the two models are compared conversation by conversation. Before the main runs, the judge was checked against answers physicians had already graded.</desc>
<g fill="none" stroke="currentColor" stroke-opacity="0.35" stroke-width="1.5">
  <rect x="20" y="10" width="320" height="60" rx="8"/>
  <rect x="20" y="210" width="212" height="76" rx="8"/>
  <rect x="20" y="320" width="320" height="70" rx="8"/>
  <rect x="246" y="210" width="94" height="76" rx="8" stroke-dasharray="4 3"/>
</g>
<rect x="20" y="104" width="150" height="72" rx="8" fill="none" stroke-width="2" style="stroke:var(--chart-opus)"/>
<rect x="190" y="104" width="150" height="72" rx="8" fill="none" stroke-width="2" style="stroke:var(--chart-gemma)"/>
<g stroke="currentColor" stroke-opacity="0.6" stroke-width="1.5" fill="none">
  <path d="M180 70 V86 H95 V100"/>
  <path d="M180 86 H265 V100"/>
  <path d="M95 176 V192 H126 V206"/>
  <path d="M265 176 V192 H126"/>
  <path d="M126 286 V316"/>
  <path d="M246 248 H236"/>
</g>
<g fill="currentColor" fill-opacity="0.6">
  <polygon points="95,104 90,96 100,96"/>
  <polygon points="265,104 260,96 270,96"/>
  <polygon points="126,210 121,202 131,202"/>
  <polygon points="126,320 121,312 131,312"/>
  <polygon points="232,248 240,243 240,253"/>
</g>
<g fill="currentColor" font-size="13">
  <text x="180" y="33" text-anchor="middle" font-size="14" font-weight="600">A HealthBench conversation</text>
  <text x="180" y="52" text-anchor="middle">where a good answer depends on who is asking</text>
  <text x="95" y="128" text-anchor="middle" font-size="14" font-weight="600">Claude Opus 5.5</text>
  <text x="95" y="148" text-anchor="middle">answers, through</text>
  <text x="95" y="165" text-anchor="middle">Anthropic's API</text>
  <text x="265" y="128" text-anchor="middle" font-size="14" font-weight="600">Gemma 3 12B</text>
  <text x="265" y="148" text-anchor="middle">answers, on</text>
  <text x="265" y="165" text-anchor="middle">a laptop</text>
  <text x="126" y="233" text-anchor="middle" font-size="14" font-weight="600">GPT-4.1, the judge</text>
  <text x="126" y="253" text-anchor="middle">checks each answer against</text>
  <text x="126" y="271" text-anchor="middle">the physicians' checklist</text>
  <text x="293" y="233" text-anchor="middle" font-size="14" font-weight="600">Checked</text>
  <text x="293" y="253" text-anchor="middle">first against</text>
  <text x="293" y="271" text-anchor="middle">physicians</text>
  <text x="180" y="343" text-anchor="middle" font-size="14" font-weight="600">A score for each answer</text>
  <text x="180" y="362" text-anchor="middle">points earned out of points possible,</text>
  <text x="180" y="380" text-anchor="middle">compared conversation by conversation</text>
</g>
</svg>
```
