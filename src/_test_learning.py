"""Quick test for the Learning Loop."""
import json
from erisia.erisia_learning import get_learning_loop

loop = get_learning_loop()
insights = loop.analyze(force=True)

print(f"Training records: {insights['training_data_count']}")
print(f"Categories: {json.dumps(insights['query_patterns']['categories'], indent=2)}")
print(f"Tool usage: {json.dumps(insights['tool_patterns']['tool_usage'], indent=2)}")
print(f"Avg prompt len: {insights['query_patterns']['avg_prompt_length']} chars")
print(f"Avg completion len: {insights['query_patterns']['avg_completion_length']} chars")
print(f"Response ratio: {insights['query_patterns']['ratio_completion_to_prompt']}x")
print(f"\nHeuristic candidates ({len(insights['heuristic_candidates'])}):")
for h in insights['heuristic_candidates']:
    print(f"  - {h[:100]}")
print(f"\nBriefing summary:")
print(loop.summary_for_briefing())
