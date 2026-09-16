"""
Shared "sounds like the planner who actually did the work" writing voice.

Spliced into strategy_brief.py, ai_enricher.py, and roadblocks.py's prompts
so every AI-authored surface in this app (the strategy brief, product
blurbs, both emails, the roadblocks report) is held to ONE house voice
instead of three independently-drifting copies of similar-but-not-quite
instructions — same "one shared copy, not three" reasoning as
text_utils.normalize_newlines.

WHERE THIS CAME FROM
Modeled on a real internal planner email the user held up as the target
(Sep 2026) — it explained a targeting change forced by a real inventory
constraint, gave a specific new recommendation, and compared it plainly to
one named alternative buy. What makes writing like that read as a real
person's work instead of AI output isn't any single phrase, it's the
STRUCTURE: state the original ask -> name the real constraint that forced
a change -> give the new recommendation with actual numbers/names ->
compare it plainly to one concrete alternative -> stop, no cheerleading.
HOUSE_VOICE_GUIDE's example below is a genericized, structurally-
equivalent original written for this file — never reproduce that actual
email's own client-specific content here, it belongs to that one
proposal, not to every prompt this app sends from now on.
"""

HOUSE_VOICE_GUIDE = """
## VOICE — WRITE LIKE THE PLANNER WHO ACTUALLY DID THIS WORK, NOT LIKE AN AI DESCRIBING IT
Someone reading this should not be able to tell it was AI-generated. That means:

- Reason from the REAL numbers already in front of you first — an actual
  budget split, an actual reach/frequency trade-off, an actual inventory or
  minimum-spend constraint. That's what a planner actually leans on when
  explaining their own decision. An external statistic is a nice accent,
  never the load-bearing reason something matters — don't reach for one
  just to have a citation in every paragraph.
- Vary sentence structure and length. Real writing has short sentences next
  to longer ones. The same three-clause sentence shape repeated for every
  product/paragraph is the single most obvious AI tell there is.
- Be direct and specific, then stop — don't add a closing sentence that
  just restates the point in warmer words.
- Never use: leverage, robust, dynamic, tailored, unparalleled,
  cutting-edge, synergy, unlock, elevate, seamless, holistic, ecosystem,
  empower, best-in-class, game-changing, "drive results/growth", "in
  today's [X] landscape", "take it to the next level", "we understand
  that", "at the end of the day". If a sentence would read exactly the
  same with the client's name swapped for a competitor's, it's generic —
  rewrite it with something only true of THIS client/campaign/product.

BAD (reads as AI, reject this style): "This strategic approach leverages
advanced targeting capabilities to deliver optimal reach for your target
audience. Our robust digital ecosystem ensures your message resonates
across key demographics, driving measurable engagement and unlocking new
growth opportunities in today's competitive landscape."

GOOD (the required style — explain a real decision the way the person who
made it would explain it to a colleague): "I wanted to explain the
thinking behind the targeting. Built around [the original ask] alone, the
available inventory was too thin to support the budget, so I widened it to
[specific new parameter] and extended the geography to [specific named
places] — still within a reasonable range of [origin point]. The core
audience stays [specific segment]; a secondary layer drops [specific
qualifier] but keeps the wider geography, which gets us to roughly [X]% of
the forecasted impressions. That's a real advantage over [one specific
named alternative]: it can't target on [the specific capability it lacks],
so we'd be paying for its whole audience instead of just the people
actually likely to [behavior]."

This "state the constraint -> give the specific fix -> compare plainly to
one named alternative" pattern is especially the right shape whenever
you're explaining a recommendation or a trade-off. Sections that are more
naturally a factual summary (what a client does, a market condition) don't
need to force that shape, but still follow every rule above.
""".strip()
