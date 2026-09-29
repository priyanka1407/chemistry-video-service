"""The verification layer: everything that decides whether a rendered video
is allowed to reach a learner, not just whether it rendered.

  claims.py           -- decompose a script's narration into atomic factual claims
  grounding.py         -- ground each factual claim against the source PDF (faithfulness gate)
  teaching_quality.py  -- rubric-scored teaching quality (rubrics/teaching_quality.yaml)
  output_review.py     -- OpenAI review of the finished video's script+narration+QC facts
  report.py            -- QualityReport: aggregates every signal into one gate decision
"""
