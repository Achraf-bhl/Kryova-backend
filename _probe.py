import re, sys
from types import SimpleNamespace
from app.catia import tool_specs as ts
from app.geometry import backends
from app.ai.tool_retrieval import select_tool_names

impl = backends.local_tool_names()
specs = [SimpleNamespace(name=s.name, description=s.description) for s in ts.CATIA_TOOL_SPECS if s.name in impl]
print("registry size:", len(specs))

prompt_names = set(re.findall(r"catia_[a-z_]+", open("app/ai/prompts.py", encoding="utf-8").read()))
prompt_names &= {s.name for s in specs}
print("tools the frozen prompts name and the registry has:", len(prompt_names))

msgs = {
 "rung3": "Design a steel mounting plate: 200 mm by 150 mm, with a 60 mm diameter bore through the centre and four 12 mm clearance holes, one 20 mm in from each corner. It has to weigh 2.4 kg. Pick a starting thickness, build it, measure the mass, and then adjust the thickness until the measured mass is within 20 grams of 2.4 kg.",
 "bolt circle": "put four M8 clearance holes on a 70 mm bolt circle",
 "mass": "make it weigh 2.4 kg",
 "clear": "check it clears through the travel",
 "plate": "Make me a steel plate 200 mm by 150 mm and 10 mm thick, with a 60 mm diameter bore through the centre. Then measure it and tell me its mass.",
}
for label, m in msgs.items():
    chosen = select_tool_names(specs, m, limit=40)
    missing = sorted(prompt_names - chosen)
    print(f"\n--- {label}: offered {len(chosen)}")
    print("   prompt-named but WITHHELD:", missing)
    for want in ("catia_set_parameter","catia_list_parameters","catia_hole","catia_pattern_circular","catia_set_material","catia_measure_between","catia_fillet"):
        if want in {s.name for s in specs}:
            print(f"   {want}: {'OFFERED' if want in chosen else 'withheld'}")
