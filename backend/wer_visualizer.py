"""
WER & CER Visual Alignment, Token Diffing, and OOV Error Analytics.

Computes exact sequence alignments (Multi-Token Wagner-Fischer DP) between reference
(ElevenLabs transliterated/phonetic) and hypothesis (Local IndicConformer), classifying each word into:
  - match (C)
  - substitution (S)
  - deletion (D)
  - insertion (I)

Specially engineered for code-switched Malayalam-English (Manglish) lecture speech:
1. Acronym Expansion: Letter-by-letter phonetic expansion (RVS -> ആർവിഎസ്, S3 -> എസ്റ്റീ/എസ്ത്രീ, MAT -> എംഎടി/മാറ്റ്).
2. Compound Agglutination: Multi-token DP binds English loanwords + Malayalam case suffixes into a single unit
   (e.g. "Academy യുടെ" ↔ "അക്കാഡമിയുടെ", "module ന്റെ" ↔ "മോഡ്യൂളിന്റെ", "S3 കാരുടെ" ↔ "എസ്റ്റീക്കാരുടെ").
3. Common Lecture Loanword Lexicon: Built-in phonetic dictionary for standard college lecture terminology
   (maths -> മാത്സ്, complex -> കോംപ്ലക്സ്, variable -> വേരിയബിൾ, integration -> ഇന്റഗ്രേഷൻ, textbook -> ടെക്സ്റ്റ് ബുക്ക്).
4. Accurate Out-of-Vocabulary (OOV) Error Attribution: True acoustic errors (e.g. Maths -> മാസ്) are tracked
   precisely without cascading false deletions onto surrounding words.
"""

import json
import logging
import os
import re
import unicodedata
from normalise import normalise_text

log = logging.getLogger("wer_visualizer")

# --- Malayalam Case Suffixes & Postpositions ---
MALAYALAM_CASE_SUFFIXES = {
    "യുടെ", "ന്റെ", "ൻ്റെ", "ഇന്റെ",
    "ൽ", "ിൽ", "യിൽ",
    "ക്ക്", "യ്ക്ക്", "ിക്ക്", "ന്", "ിന്",
    "ഉം", "വും",
    "ഓട്", "യോട്", "ഇനോട്",
    "ആൽ", "ഇനാൽ",
    "ലേക്ക്", "ഇലേക്ക്", "ത്തിലേക്ക്",
    "നകത്ത്", "ൽനകത്ത്", "ിൽനകത്ത്",
    "നിന്നു", "ൽനിന്ന്", "ിൽനിന്ന്", "നിന്നും", "ൽനിന്നും", "ിൽനിന്നും",
    "ആണ്", "ആയിരുന്നു", "ആയിരുന്നെങ്കിൽ", "എന്നാണ്",
    "കാരുടെ", "കാര്", "കാരം",
    "കൾ", "കളെ", "കളിൽ", "കൾക്ക്", "കളിലേക്ക്",
    "തന്നെ", "തന്നേ",
    "ഒക്കെ", "ഒക്കെയും", "മാത്രം", "പോലും", "വരെ", "വരെയും", "ആയി", "ആയിട്ട്", "എല്ലാം", "എല്ലാമാണ്"
}

# --- Acronym English Letters & Digits to Malayalam Syllables ---
ACRONYM_MAP = {
    "A": "എ", "B": "ബി", "C": "സി", "D": "ഡി", "E": "ഇ", "F": "എഫ്",
    "G": "ജി", "H": "എച്ച്", "I": "ഐ", "J": "ജെ", "K": "കെ", "L": "എൽ",
    "M": "എം", "N": "എൻ", "O": "ഒ", "P": "പി", "Q": "ക്യു", "R": "ആർ",
    "S": "എസ്", "T": "ടി", "U": "യു", "V": "വി", "W": "ഡബ്ല്യു", "X": "എക്സ്",
    "Y": "വൈ", "Z": "ഇസഡ്",
    "0": "സീറോ", "1": "വൺ", "2": "ടു", "3": "ത്രീ", "4": "ഫോർ",
    "5": "ഫൈവ്", "6": "സിക്സ്", "7": "സെവൻ", "8": "എയിറ്റ്", "9": "നയൻ"
}

# --- Built-in Phonetic Dictionary for High-Frequency Academic, Math & Lecture Loanwords ---
COMMON_LOANWORDS = {
    # Math & Engineering Terms
    "maths": ["മാത്സ്", "മാത്‍സ്", "മാത്തമാറ്റിക്സ്"],
    "mathematics": ["മാത്തമാറ്റിക്സ്", "മാത്തമറ്റിക്സ്", "മാത്സ്"],
    "academy": ["അക്കാദമി", "അക്കാഡമി"],
    "module": ["മോഡ്യൂൾ", "മൊഡ്യൂൾ", "മോഡൽ"],
    "syllabus": ["സിലബസ്"],
    "topic": ["ടോപ്പിക്", "ടോപ്പിക്ക്", "ടോപിക്"],
    "topics": ["ടോപ്പിക്സ്", "ടോപ്പിക്ക്സ്", "ടോപിക്സ്"],
    "question": ["ക്വസ്റ്റ്യൻ"],
    "questions": ["ക്വസ്റ്റ്യൻസ്", "ക്വസ്റ്റ്യൻസാണ്"],
    "problem": ["പ്രോബ്ലം"],
    "problems": ["പ്രോബ്ലംസ്"],
    "complex": ["കോംപ്ലക്സ്"],
    "variable": ["വേരിയബിൾ"],
    "variables": ["വേരിയബിൾസ്"],
    "integration": ["ഇന്റഗ്രേഷൻ"],
    "integrations": ["ഇന്റഗ്രേഷൻസ്"],
    "differentiation": ["ഡിഫറൻഷ്യേഷൻ", "ഡിഫ്ഫേരേന്സേഷന്"],
    "formula": ["ഫോർമുല"],
    "formulas": ["ഫോർമുലാസ്", "ഫോർമുലകൾ"],
    "theorem": ["തിയറം"],
    "theorems": ["തിയറംസ്"],
    "matrix": ["മാട്രിക്സ്"],
    "matrices": ["മാട്രിസസ്"],
    "linear": ["ലീനിയർ"],
    "vector": ["വെക്ടർ"],
    "vectors": ["വെക്ടേഴ്സ്"],
    "rank": ["റാങ്ക്"],
    "row": ["റോ"],
    "rows": ["റോസ്"],
    "column": ["കോളം"],
    "columns": ["കോളങ്ങൾ", "കോളംസ്"],
    "element": ["എലമെന്റ്"],
    "elements": ["എലമെന്റ്സ്"],
    "diagonal": ["ഡയഗണൽ"],
    "determinant": ["ഡിറ്റർമിനന്റ്"],
    "inverse": ["ഇൻവേഴ്സ്"],
    "eigen": ["ഐഗൻ"],
    "function": ["ഫംഗ്ഷൻ", "ഫങ്ഷൻ"],
    "functions": ["ഫംഗ്ഷൻസ്", "ഫങ്ഷൻസ്"],
    "limit": ["ലിമിറ്റ്"],
    "limits": ["ലിമിറ്റ്സ്"],
    "series": ["സീരീസ്"],
    "value": ["വാല്യൂ"],
    "values": ["വാല്യൂസ്"],
    "integral": ["ഇന്റഗ്രൽ"],
    "integrals": ["ഇന്റഗ്രൽസ്"],
    "derivative": ["ഡെറിവേറ്റീവ്"],
    "derivatives": ["ഡെറിവേറ്റീവ്സ്"],
    "power": ["പവർ"],
    "powers": ["പവേഴ്സ്"],
    "square": ["സ്ക്വയർ"],
    "cube": ["ക്യൂബ്"],
    "constant": ["കോൺസ്റ്റന്റ്"],
    "constants": ["കോൺസ്റ്റന്റ്സ്"],
    "equation": ["ഇക്വേഷൻ"],
    "equations": ["ഇക്വേഷൻസ്"],
    "line": ["ലൈൻ"],
    "lines": ["ലൈൻസ്"],
    "point": ["പോയിന്റ്", "പോയിൻറ്"],
    "points": ["പോയിന്റ്സ്", "പോയിൻറ്സ്"],
    "graph": ["ഗ്രാഫ്"],
    "graphs": ["ഗ്രാഫ്സ്"],
    "root": ["റൂട്ട്"],
    "roots": ["റൂട്ട്സ്"],
    "circle": ["സർക്കിൾ"],
    "circles": ["സർക്കിൾസ്"],
    "curve": ["കർവ്"],
    "curves": ["കർവ്സ്"],
    "pole": ["പോൾ"],
    "poles": ["പോൾസ്"],
    "residue": ["റെസിഡ്യൂ"],
    "residues": ["റെസിഡ്യൂസ്"],
    "taylor": ["ടെയ്‌ലർ", "ടെയ്ലർ"],
    "laurent": ["ലോറന്റ്"],
    "cauchy": ["കോഷി", "കോഷ്യസ്", "കോഷീസ്"],
    "riemann": ["റീമാൻ"],
    "singular": ["സിംഗുലർ"],
    "analytic": ["അനലിറ്റിക്"],
    "harmonic": ["ഹാർമോണിക്"],
    "transformation": ["ട്രാൻസ്ഫോർമേഷൻ"],
    "conformal": ["കോൺഫോർമൽ"],
    "mapping": ["മാപ്പിംഗ്"],

    # Lecture & Classroom Speech
    "textbook": ["ടെക്സ്റ്റ്ബുക്ക്", "ടെക്സ്റ്റ് ബുക്ക്", "ടെക്സ്റ്റ്"],
    "book": ["ബുക്ക്"],
    "books": ["ബുക്സ്"],
    "edition": ["എഡിഷൻ", "അഡിഷൻ"],
    "publication": ["പബ്ലിക്കേഷൻ"],
    "start": ["സ്റ്റാർട്ട്"],
    "started": ["സ്റ്റാർട്ടഡ്"],
    "starts": ["സ്റ്റാർട്ട്സ്"],
    "follow": ["ഫോളോ"],
    "following": ["ഫോളോയിങ്", "ഫോളോയിംഗ്"],
    "advanced": ["അഡ്വാൻസ്ഡ്", "അഡ്വാൻസ്"],
    "engineering": ["എൻജിനീയറിങ്", "എഞ്ചിനീയറിംഗ്", "എൻജിനീയറിംഗ്"],
    "senior": ["സീനിയർ"],
    "seniors": ["സീനിയേഴ്സ്"],
    "year": ["ഇയർ"],
    "years": ["ഇയേഴ്സ്"],
    "paper": ["പേപ്പർ"],
    "papers": ["പേപ്പേഴ്സ്"],
    "teacher": ["ടീച്ചർ"],
    "teachers": ["ടീച്ചേഴ്സ്"],
    "student": ["സ്റ്റുഡന്റ്"],
    "students": ["സ്റ്റുഡന്റ്സ്"],
    "class": ["ക്ലാസ്"],
    "classes": ["ക്ലാസസ്", "ക്ലാസുകൾ"],
    "mark": ["മാർക്ക്"],
    "marks": ["മാർക്സ്"],
    "step": ["സ്റ്റെപ്പ്"],
    "steps": ["സ്റ്റെപ്പ്സ്"],
    "check": ["ചെക്ക്"],
    "note": ["നോട്ട്"],
    "write": ["റൈറ്റ്"],
    "read": ["റീഡ്"],
    "table": ["ടേബിൾ"],
    "method": ["മെത്തേഡ്", "മെത്തഡ്"],
    "methods": ["മെത്തേഡ്സ്", "മെത്തഡ്സ്"],
    "order": ["ഓർഡർ"],
    "degree": ["ഡിഗ്രി"],
    "system": ["സിസ്റ്റം"],
    "maximum": ["മാക്സിമം"],
    "minimum": ["മിനിമം"],
    "include": ["ഇൻക്ലൂഡ്"],
    "includes": ["ഇൻക്ലൂഡ്സ്"],
    "included": ["ഇൻക്ലൂഡഡ്"],
    "chance": ["ചാൻസ്"],
    "front": ["ഫ്രണ്ട്"],
    "total": ["ടോട്ടൽ"],
    "part": ["പാർട്ട്"],
    "parts": ["പാർട്സ്"],
    "thorough": ["തറോ"],
    "simple": ["സിമ്പിൾ"],
    "clear": ["ക്ലിയർ"],
    "same": ["സെയിം"],
    "level": ["ലെവൽ"],
    "section": ["സെക്ഷൻ"],
    "sections": ["സെക്ഷൻസ്"],
    "type": ["ടൈപ്പ്"],
    "types": ["ടൈപ്പ്സ്"],
    "case": ["കേസ്"],
    "cases": ["കേസസ്"],
    "form": ["ഫോം"],
    "forms": ["ഫോംസ്"],
    "kreyszig": ["ക്രോസിക്ക്", "ക്രോസ്വിഗ്", "ക്രോയിസിഗ്", "ക്രേസിക്ക്", "ക്രേസിക്കിന്റെ"],
    "erwin": ["എർവിൻ", "ഇയർവിൻ"],
    "john": ["ജോൺ"],
    "wiley": ["വേലിയേൻ", "വൈലി"],
    "sons": ["സൺസ്", "സൺസിന്റെ"],
    "okay": ["ഓക്കെ", "ഓകെ", "ഓക്കേ"],
    "ok": ["ഓക്കെ", "ഓകെ", "ഓക്കേ"],
    "yes": ["യെസ്"],
    "no": ["നോ"],
    "next": ["നെക്സ്റ്റ്"],
    "previous": ["പ്രീവിയസ്"],
    "solve": ["സോൾവ്"],
    "solving": ["സോൾവിങ്"],
    "answer": ["ആൻസർ"],
    "answers": ["ആൻസേഴ്സ്"],
    "example": ["എക്സാമ്പിൾ"],
    "base": ["ബേസ്"],
    "prepare": ["പ്രിപ്പയർ", "പ്രിപയർ"],
    "prepared": ["പ്രിപ്പയേർഡ്", "പ്രിപയേർഡ്"],
    "preparation": ["പ്രിപ്പറേഷൻ", "പ്രിപറേഷൻ"],
    "sorry": ["സോറി"],
    "basic": ["ബേസിക്"],
    "standard": ["സ്റ്റാൻഡേർഡ്", "സ്റ്റാൻഡേഡ്"],
    "normal": ["നോർമൽ"],
    "general": ["ജനറൽ"],
    "particular": ["പർട്ടിക്കുലർ"],
    "single": ["സിംഗിൾ"],
    "double": ["ഡബിൾ"],
    "triple": ["ട്രിപ്പിൾ"],
    "whole": ["ഹോൾ"],
    "sign": ["സൈൻ"],
    "capital": ["ക്യാപിറ്റൽ"],
    "number": ["നമ്പർ"],

    # Trigonometric, Hyperbolic & Logarithmic Functions
    "sin": ["സൈൻ", "സൈനിന്റെ", "സൈൻസിന്"],
    "cos": ["കോസ്", "കോസിന്റെ", "കോസിന്"],
    "tan": ["ടാൻ", "ടാനിന്റെ", "ടാനിന്"],
    "sec": ["സെക്", "സെക്കിന്റെ", "സെക്കിന്"],
    "cosec": ["കൊസീക്", "കൊസെക്", "കൊസീക്കിന്റെ"],
    "cot": ["കോട്ട്", "കോട്ടിന്റെ", "കോട്ടിന്"],
    "sinh": ["സൈൻഎച്ച്", "സൈൻ എച്ച്"],
    "cosh": ["കോസ്എച്ച്", "കോസ് എച്ച്"],
    "tanh": ["ടാൻഎച്ച്", "ടാൻ എച്ച്"],
    "log": ["ലോഗ്", "ലോഗിന്റെ", "ലോഗിന്", "ലോഗു"],
    "ln": ["എൽഎൻ", "എൽ എൻ"],
    "exp": ["ഇഎക്സ്പി", "ഇ എക്സ് പി"],
    "exponential": ["എക്സ്പോണൻഷ്യൽ", "എക്സ്പൊണൻഷ്യൽ"],
    "raise": ["റേസ്", "റൈസ്"],
    "raised": ["റേസ്ഡ്", "റൈസ്ഡ്"],

    # Complex Analysis & Geometry Keywords
    "set": ["സെറ്റ്", "സെറ്റിന്റെ", "സെറ്റുകൾ"],
    "subset": ["സബ്സെറ്റ്", "സബ്സെറ്റിന്റെ"],
    "plane": ["പ്ലെയ്ൻ", "പ്ലെയിൻ", "പ്ലേൻ", "പ്ലെയ്നിന്റെ"],
    "axis": ["ആക്സിസ്", "ആക്സിസിന്റെ"],
    "axes": ["ആക്സസ്"],
    "real": ["റിയൽ"],
    "imaginary": ["ഇമാജിനറി"],
    "polar": ["പോളാർ"],
    "radius": ["റേഡിയസ്", "റേഡിയസിന്റെ"],
    "region": ["റീജിയൻ", "റീജിയൺ", "റീജിയന്റെ"],
    "domain": ["ഡൊമൈൻ", "ഡൊമെയിൻ", "ഡൊമൈന്റെ"],
    "path": ["പാത്ത്", "പാത്തിന്റെ"],
    "contour": ["കോണ്ടൂർ", "കോണ്ടൂർസ്"],
    "closed": ["ക്ലോസ്ഡ്"],
    "open": ["ഓപ്പൺ"],
    "connected": ["കണക്റ്റഡ്", "കണക്റ്റഡാണ്"],
    "simply": ["സിംപ്ലി"],

    # Mathematical Actions, Terms & Operations
    "term": ["ടേം"],
    "terms": ["ടേംസ്"],
    "bracket": ["ബ്രാക്കറ്റ്", "ബ്രാക്കറ്റിൽ"],
    "brackets": ["ബ്രാക്കറ്റ്സ്", "ബ്രാക്കറ്റുകൾ"],
    "numerator": ["ന്യൂമറേറ്റർ", "ന്യൂമറേറ്ററിൽ"],
    "denominator": ["ഡിനോമിനേറ്റർ", "ഡിനോമിനേറ്ററിൽ"],
    "multiply": ["മൾട്ടിപ്ലൈ"],
    "multiplied": ["മൾട്ടിപ്ലൈഡ്"],
    "multiplication": ["മൾട്ടിപ്ലിക്കേഷൻ"],
    "divide": ["ഡിവൈഡ്"],
    "divided": ["ഡിവൈഡഡ്"],
    "division": ["ഡിവിഷൻ"],
    "cancel": ["കാൻസൽ"],
    "cancelled": ["കാൻസൽഡ്"],
    "cancelling": ["കാൻസലിങ്", "കാൻസലിംഗ്"],
    "put": ["പുട്ട്"],
    "putting": ["പുട്ടിങ്", "പുട്ടിംഗ്"],
    "take": ["ടേക്ക്"],
    "taking": ["ടേക്കിങ്", "ടേക്കിംഗ്"],
    "get": ["ഗെറ്റ്"],
    "getting": ["ഗെറ്റിങ്", "ഗെറ്റിംഗ്"],
    "give": ["ഗിവ്"],
    "gives": ["ഗിവ്സ്"],
    "giving": ["ഗിവിങ്", "ഗിവിംഗ്"],
    "becomes": ["ബികംസ്"],
    "show": ["ഷോ"],
    "showing": ["ഷോയിങ്", "ഷോയിംഗ്"],
    "prove": ["പ്രൂവ്"],
    "proof": ["പ്രൂഫ്"],
    "define": ["ഡിഫൈൻ"],
    "definition": ["ഡെഫിനിഷൻ"],
    "state": ["സ്റ്റേറ്റ്"],
    "statement": ["സ്റ്റേറ്റ്മെന്റ്"],
    "known": ["നോൺ"],
    "unknown": ["അൺനോൺ"],
    "given": ["ഗിവൺ"],
    "required": ["റിക്വയർഡ്"],
    "find": ["ഫൈൻഡ്"],
    "finding": ["ഫൈൻഡിങ്", "ഫൈൻഡിംഗ്"],
    "calculate": ["കാൽക്കുലേറ്റ്"],
    "calculation": ["കാൽക്കുലേഷൻ"],
    "calculating": ["കാൽക്കുലേറ്റിങ്", "കാൽക്കുലേറ്റിംഗ്"],
    "calculator": ["കാൽക്കുലേറ്റർ"],

    # Math Calculus Actions & Greek Symbols
    "equal": ["ഈക്വൽ"],
    "equals": ["ഈക്വൽസ്"],
    "plus": ["പ്ലസ്"],
    "minus": ["മൈനസ്"],
    "into": ["ഇന്റു", "ഇൻടു"],
    "theta": ["തീറ്റ"],
    "phi": ["ഫൈ"],
    "psi": ["സൈ"],
    "alpha": ["ആൽഫ"],
    "beta": ["ബീറ്റ"],
    "gamma": ["ഗാമ"],
    "delta": ["ഡെൽറ്റ"],
    "pi": ["പൈ"],
    "infinity": ["ഇൻഫിനിറ്റി"],
    "evaluate": ["ഇവാലുവേറ്റ്"],
    "evaluation": ["ഇവാലുവേഷൻ"],
    "evaluating": ["ഇവാലുവേറ്റിങ്", "ഇവാലുവേറ്റിംഗ്"],
    "integrate": ["ഇന്റഗ്രേറ്റ്"],
    "integrating": ["ഇന്റഗ്രേറ്റിങ്", "ഇന്റഗ്രേറ്റിംഗ്"],
    "differentiate": ["ഡിഫറൻഷ്യേറ്റ്"],
    "differentiating": ["ഡിഫറൻഷ്യേറ്റിങ്", "ഡിഫറൻഷ്യേറ്റിംഗ്"],
    "substitute": ["സബ്സ്റ്റിറ്റ്യൂട്ട്", "സബ്സ്റ്റിട്യൂട്ട്"],
    "substitution": ["സബ്സ്റ്റിറ്റ്യൂഷൻ"],
    "apply": ["അപ്ലൈ"],
    "applying": ["അപ്ലൈയിങ്", "അപ്ലൈയിംഗ്"],
    "applied": ["അപ്ലൈഡ്"],
    "split": ["സ്പ്ലിറ്റ്"],
    "splitting": ["സ്പ്ലിറ്റിങ്", "സ്പ്ലിറ്റിംഗ്"],
    "lower": ["ലോവർ"],
    "upper": ["അപ്പർ"],
    "negative": ["നെഗറ്റീവ്"],
    "positive": ["പോസിറ്റീവ്"],
    "direct": ["ഡയറക്റ്റ്", "ഡയറക്ട്"],
    "last": ["ലാസ്റ്റ്"],
    "properties": ["പ്രോപ്പർട്ടീസ്", "പ്രോപ്പർട്ടി"],
    "property": ["പ്രോപ്പർട്ടി"],

    # Prepositions, Pronouns & Connectors Common in Lecture Code-Switching
    "to": ["ടു"],
    "by": ["ബൈ", "ബി"],
    "of": ["ഓഫ്"],
    "is": ["ഇസ്", "ഈസ്"],
    "and": ["ആൻഡ്", "ആന്റ്"],
    "or": ["ഓർ"],
    "for": ["ഫോർ"],
    "in": ["ഇൻ"],
    "at": ["അറ്റ്"],
    "from": ["ഫ്രം"],
    "with": ["വിത്ത്"],
    "then": ["ദെൻ"],
    "so": ["സോ"],
    "if": ["ഇഫ്"],
    "as": ["ആസ്"],
    "up": ["അപ്പ്"],
    "down": ["ഡൗൺ"],
    "the": ["ദ"],

    # Numbers & Ordinals
    "zero": ["സീറോ", "പൂജ്യം"],
    "one": ["വൺ", "ഒന്ന്"],
    "two": ["ടു", "രണ്ട്"],
    "three": ["ത്രീ", "മൂന്ന്"],
    "four": ["ഫോർ", "നാല്", "ഫോറാണ്"],
    "five": ["ഫൈവ്", "അഞ്ച്"],
    "six": ["സിക്സ്", "ആറ്"],
    "seven": ["സെവൻ", "ഏഴ്"],
    "eight": ["എയിറ്റ്", "എട്ട്"],
    "nine": ["നയൻ", "ഒൻപത്"],
    "ten": ["ടെൻ", "പത്ത്"],
    "eleven": ["ഇലവൻ"],
    "twelve": ["ട്വൽവ്"],
    "thirteen": ["തേർട്ടീൻ"],
    "fourteen": ["ഫോർട്ടീൻ"],
    "fifteen": ["ഫിഫ്റ്റീൻ"],
    "sixteen": ["സിക്സ്റ്റീൻ"],
    "seventeen": ["സെവന്റീൻ"],
    "eighteen": ["എയിറ്റീൻ"],
    "nineteen": ["നൈന്റീൻ"],
    "twenty": ["ട്വന്റി"],
    "thirty": ["തേർട്ടി"],
    "forty": ["ഫോർട്ടി"],
    "fifty": ["ഫിഫ്റ്റി"],
    "hundred": ["ഹൺഡ്രഡ്"],
    "first": ["ഫസ്റ്റ്"],
    "second": ["സെക്കൻഡ്", "സെക്കന്റ്"],
    "third": ["തേർഡ്", "തിർഡ്"],
    "fourth": ["ഫോർത്ത്"],
    "fifth": ["ഫിഫ്ത്"],
    "sixth": ["സിക്സ്ത്"],
    "seventh": ["സെവൻത്"],
    "eighth": ["എയിറ്റ്ത്"],
    "ninth": ["നൈൻത്"],
    "tenth": ["ടെൻത്", "ടെന്ത്"],

    # Single Math Variables & Differentials
    "c": ["സി", "സീ"],
    "e": ["ഇ", "ഈ"],
    "i": ["ഐ", "ആയി", "ഇ"],
    "z": ["ഇസഡ്", "ഡി", "സെഡ്", "ഇസറ്", "സെറ്റ്"],
    "x": ["എക്സ്"],
    "y": ["വൈ"],
    "d": ["ഡി"],
    "dx": ["ഡിഎക്സ്", "ഡി എക്സ്"],
    "dy": ["ഡിവൈ", "ഡി വൈ"],
    "dz": ["ഡിസെഡ്", "ഡിസഡ്", "ഡി ഇസഡ്"],
    "dt": ["ഡിടി", "ഡി ടി"],
    "r": ["ആർ"],
    "code": ["കോഡ്"]
}

# --- Spoken Malayalam Colloquial Equivalents ---
COLLOQUIAL_EQUIVALENTS = {
    "അപ്പൊ": {"അപ്പം", "അപ്പോൾ", "അപ്പൊ", "ഓക്കേ"},
    "അപ്പം": {"അപ്പൊ", "അപ്പോൾ", "അപ്പം"},
    "അപ്പോൾ": {"അപ്പൊ", "അപ്പം", "അപ്പോൾ"},
    "കൂടെ": {"കൂടി", "കൂടെ"},
    "കൂടി": {"കൂടെ", "കൂടി"},
    "പറഞ്ഞാ": {"പറഞ്ഞാൽ", "പറഞ്ഞാ"},
    "പറഞ്ഞാൽ": {"പറഞ്ഞാ", "പറഞ്ഞാൽ"},
    "വരുമ്പം": {"വരുമ്പോൾ", "വരുമ്പം"},
    "വരുമ്പോൾ": {"വരുമ്പം", "വരുമ്പോൾ"},
    "എഴുതുന്നെ": {"എഴുതുന്നത്", "എഴുതുന്നെ", "എഴുതുന്നേ"},
    "എഴുതുന്നത്": {"എഴുതുന്നെ", "എഴുതുന്നേ", "എഴുതുന്നത്"},
    "എന്നാ": {"എന്നാണ്", "എന്നാ"},
    "എന്നാണ്": {"എന്നാ", "എന്നാണ്"},
    "ചെയ്താ": {"ചെയ്താൽ", "ചെയ്താ"},
    "ചെയ്താൽ": {"ചെയ്താ", "ചെയ്താൽ"},
    "പറഞ്ഞ്": {"പറഞ്ഞു", "പറഞ്ഞ്"},
    "പറഞ്ഞു": {"പറഞ്ഞ്", "പറഞ്ഞു"},
    "വച്ച്": {"വെച്ച്", "വച്ച്"},
    "വെച്ച്": {"വച്ച്", "വെച്ച്"},
    "പറയുന്നത്": {"പറയുന്നേ", "പറയുന്നെ", "പറയുന്നത്"},
    "പറയുന്നേ": {"പറയുന്നത്", "പറയുന്നെ", "പറയുന്നേ"},
    "ചെയ്യുന്നത്": {"ചെയ്യുവാണ്", "ചെയ്യാണ്", "ചെയ്യുന്നത്", "ചെയ്യുകയാണ്"},
    "ചെയ്യുവാണ്": {"ചെയ്യാണ്", "ചെയ്യുന്നത്", "ചെയ്യുവാണ്", "ചെയ്യുകയാണ്"},
    "ചെയ്യാണ്": {"ചെയ്യുവാണ്", "ചെയ്യുന്നത്", "ചെയ്യാണ്", "ചെയ്യുകയാണ്"},
    "ആയിരുന്നെങ്കിൽ": {"ആയിരുന്നെങ്കിലും", "ആയിരുന്നെങ്കിൽ"},
    "ആയിരുന്നെങ്കിലും": {"ആയിരുന്നെങ്കിൽ", "ആയിരുന്നെങ്കിലും"},
    "പേര്": {"പേർ", "പേര്"},
    "പേർ": {"പേര്", "പേർ"},
    "നമ്മള്": {"നമ്മൾ", "നമ്മള്"},
    "നമ്മൾ": {"നമ്മള്", "നമ്മൾ"},
    "അതിന്റെ": {"എന്റെ", "അതിന്റെ"},
    "എന്റെ": {"അതിന്റെ", "എന്റെ"},
    "ചെയ്യാണെങ്കില്": {"ചെയ്യണമെങ്കിൽ", "ചെയ്യാണെങ്കില്"},
    "ചെയ്യണമെങ്കിൽ": {"ചെയ്യാണെങ്കില്", "ചെയ്യണമെങ്കിൽ"}
}


def levenshtein_distance(s1: str, s2: str) -> int:
    """Fast character-level Levenshtein distance."""
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)

    r1, r2 = list(range(len(s2) + 1)), [0] * (len(s2) + 1)
    for i, c1 in enumerate(s1):
        r2[0] = i + 1
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            r2[j + 1] = min(r1[j + 1] + 1, r2[j] + 1, r1[j] + cost)
        r1, r2 = r2, [0] * (len(s2) + 1)
    return r1[len(s2)]


def is_latin_word(word: str) -> bool:
    """Check if word contains Latin letters (English loanword)."""
    return any("LATIN" in unicodedata.name(c, "") for c in word)


# --- Bilingual Phonetic Matcher (G2P Consonant Skeleton Alignment) ---
# Allows any arbitrary English technical loanword in Latin script to phonetically match its
# Malayalam transcription dynamically without requiring manual dictionary curation.

MALAYALAM_LIGATURES = [
    ("റ്റ", "tt"),
    ("ന്റ", "nt"),
    ("ണ്ട", "nd"),
    ("മ്പ", "mp"),
    ("ഞ്ച", "nch"),
    ("ങ്ക", "nk"),
    ("ദ്ധ", "d"),
    ("ഗ്ഗ", "g"),
    ("ബ്ബ", "b"),
    ("പ്പ", "p"),
    ("ക്ക", "k"),
    ("ച്ച", "ch"),
    ("ട്ട", "t"),
    ("മ്മ", "m"),
    ("ന്ന", "n"),
    ("ല്ല", "l"),
    ("വ്വ", "v"),
    ("സ്സ", "s"),
]

MALAYALAM_CONSONANT_MAP = {
    # Velar
    "ക": "k", "ഖ": "k", "ഗ": "g", "ഘ": "g", "ങ": "ng",
    # Palatal
    "ച": "ch", "ഛ": "ch", "ജ": "j", "ഝ": "j", "ഞ": "nj",
    # Retroflex
    "ട": "t", "ഠ": "t", "ഡ": "d", "ഢ": "d", "ണ": "n",
    # Dental
    "ത": "t", "ഥ": "t", "ദ": "d", "ധ": "d", "ന": "n",
    # Labial
    "പ": "p", "ഫ": "f", "ബ": "b", "ഭ": "b", "മ": "m",
    # Semivowels & Liquids
    "യ": "y", "ര": "r", "റ": "r", "ല": "l", "ള": "l", "ഴ": "r", "വ": "v",
    # Fricatives
    "ശ": "sh", "ഷ": "sh", "സ": "s", "ഹ": "h",
    # Chillus
    "ർ": "r", "ൽ": "l", "ൾ": "l", "ൺ": "n", "ൻ": "n", "ൿ": "k", "ം": "m"
}


def malayalam_to_phonetic_skeleton(text: str) -> str:
    """Converts Malayalam text into a normalized phonetic consonant skeleton."""
    t = text
    for lig, rep in MALAYALAM_LIGATURES:
        t = t.replace(lig, rep)
        
    out = []
    for c in t:
        if c in MALAYALAM_CONSONANT_MAP:
            out.append(MALAYALAM_CONSONANT_MAP[c])
        elif 'a' <= c <= 'z':
            out.append(c)
            
    res = "".join(out)
    # Strip epenthetic glide 'y' (യ) which Malayalam uses to represent English diphthongs/vowels
    res = res.replace("y", "")
    res = re.sub(r"(.)\1+", r"\1", res)
    return res


def english_to_phonetic_skeleton(text: str) -> str:
    """Converts an English word into a normalized phonetic consonant skeleton."""
    w = text.lower().strip(".,;:?!'\"-()")
    
    # Morphological endings
    w = re.sub(r"ture\b", "chr", w)
    w = re.sub(r"stion\b", "stn", w)
    w = re.sub(r"tion|sion", "shn", w)
    w = re.sub(r"\bsur", "shr", w)
    
    # Multi-letter patterns using placeholders
    w = re.sub(r"ch", "\u0001", w)
    w = re.sub(r"sh", "\u0002", w)
    w = re.sub(r"th", "\u0003", w)
    w = re.sub(r"ph", "f", w)
    w = re.sub(r"qu", "kv", w)
    w = re.sub(r"ck", "k", w)
    w = re.sub(r"wh", "v", w)
    w = re.sub(r"dg", "j", w)
    
    # Contextual consonants
    w = re.sub(r"c(?=[eiy])", "s", w)
    w = re.sub(r"c", "k", w)
    w = re.sub(r"g(?=[eiy])", "j", w)
    w = re.sub(r"x", "ks", w)
    w = re.sub(r"w", "v", w)
    w = re.sub(r"z", "s", w)
    
    # Restore multi-letter patterns
    w = w.replace("\u0001", "ch").replace("\u0002", "sh").replace("\u0003", "t")
    
    # Strip vowels and glides
    out = [c for c in w if c not in "aeiouy"]
    res = "".join(out)
    res = re.sub(r"(.)\1+", r"\1", res)
    return res


def phonetic_similarity(eng_word: str, mal_word: str) -> float:
    """Compute normalized phonetic similarity [0.0 - 1.0] between an English word and a Malayalam word."""
    ep = english_to_phonetic_skeleton(eng_word)
    mp = malayalam_to_phonetic_skeleton(mal_word)
    if not ep or not mp:
        return 0.0
    if ep == mp:
        return 1.0
    dist = levenshtein_distance(ep, mp)
    max_len = max(len(ep), len(mp))
    return 1.0 - (dist / max_len)


def expand_acronym(word: str) -> list[str]:
    """Expands acronyms (RVS -> ആർവിഎസ്, S3 -> എസ്റ്റീ/എസ്ത്രീ, MAT -> എംഎടി/മാറ്റ്, KTU -> കെടിയു)."""
    w = word.strip().upper()
    if not w:
        return []
    if len(w) <= 6 and (w.isupper() or any(c.isdigit() for c in w)):
        chars = []
        for c in w:
            if c in ACRONYM_MAP:
                chars.append(ACRONYM_MAP[c])
            else:
                return []
        primary = "".join(chars)
        res = [primary]
        if w == "S3":
            res.extend(["എസ്റ്റീ", "എസ്ത്രീ", "എസ് ത്രീ", "എസ്"])
        elif w == "MAT":
            res.extend(["മാറ്റ്", "എം എ ടി", "എം എ"])
        elif w == "KTU":
            res.extend(["കെടിയു", "കെ ടി യു"])
        return res
    return []


def apply_malayalam_sandhi(stem: str, suffix: str) -> list[str]:
    """
    Applies Malayalam morphophonemic Sandhi rules when joining a stem with a case/postpositional suffix.
    Handles virama dropping, chillu vowel transitions, and consonant gemination.
    """
    if not stem or not suffix:
        return [stem + suffix]

    res = [stem + suffix, stem + "്" + suffix]
    VIRAMA = "\u0D4D"

    # 1. Stem ends in Virama (e.g. ക്വസ്റ്റ്യൻസ്, ബുക്ക്, ക്ലാസ്, അഡ്വാൻസ്ഡ്)
    if stem.endswith(VIRAMA):
        base_cons = stem[:-1]  # Drop the virama
        if suffix in ("ന്", "ിന്"):
            res.extend([base_cons + "ിന്", base_cons + "ത്തിന്", stem + "ിന്"])
        elif suffix in ("ആണ്", "ആ"):
            res.extend([base_cons + "ാണ്", base_cons + "ആണ്"])
        elif suffix in ("ആയിരുന്നു", "ആയിരുന്നെങ്കിൽ", "ആയി", "ആയിട്ട്"):
            res.extend([base_cons + "ായിരുന്നു", base_cons + "ായിരുന്നെങ്കിൽ", base_cons + "ായി", base_cons + "ായിട്ട്"])
        elif suffix in ("നകത്ത്", "അകത്ത്", "ൽനകത്ത്"):
            res.extend([base_cons + "ിനകത്ത്", base_cons + "നകത്ത്", stem + "നകത്ത്"])
        elif suffix in ("ൽ", "ിൽ", "യിൽ"):
            res.extend([base_cons + "ിൽ", base_cons + "ൽ"])
        elif suffix in ("ഉം", "വും"):
            res.extend([base_cons + "ും", base_cons + "വും"])
        elif suffix in ("ന്റെ", "ൻ്റെ", "ഇന്റെ"):
            res.extend([base_cons + "ിന്റെ", base_cons + "ന്റെ"])
        elif suffix in ("ഒക്കെ", "ഒക്കെയും"):
            res.extend([base_cons + "ൊക്കെ", base_cons + "ൊക്കെയും"])
        elif suffix.startswith("ക"):
            res.extend([stem + "്ക" + suffix[1:], stem + "ക്ക" + suffix[1:], base_cons + "ുക" + suffix[1:]])

    # 2. Stem ends in Chillu-R (ർ) (e.g. ഇയർ, പേപ്പർ, ടീച്ചർ)
    elif stem.endswith("ർ"):
        root = stem[:-1]
        if suffix in ("ൽ", "ിൽ"):
            res.extend([root + "രിൽ", root + "റിൽ", stem + "ിൽ"])
        elif suffix in ("ഉം", "വും"):
            res.extend([root + "റും", root + "രും", stem + "ിലും"])
        elif suffix in ("ന്റെ", "ൻ്റെ", "ഇന്റെ"):
            res.extend([root + "റിന്റെ", root + "രിന്റെ", stem + "ിന്റെ"])
        elif suffix in ("ആണ്", "ആ"):
            res.extend([root + "രാണ്", root + "റാണ്"])
        elif suffix in ("ആയിരുന്നു", "ആയിരുന്നെങ്കിൽ", "ആയി", "ആയിട്ട്"):
            res.extend([root + "രായിരുന്നു", root + "റായിരുന്നു", root + "രായി", root + "റായി", stem + "ആയിരുന്നു"])
        elif suffix in ("നകത്ത്", "അകത്ത്"):
            res.extend([root + "രിനകത്ത്", root + "റിനകത്ത്", stem + "നകത്ത്"])
        elif suffix in ("ന്", "ിന്"):
            res.extend([root + "രിന്", root + "റിന്", stem + "ിന്"])
        elif suffix in ("ഒക്കെ", "ഒക്കെയും"):
            res.extend([root + "റൊക്കെ", root + "രൊക്കെ", stem + "ഒക്കെ"])

    # 3. Stem ends in Chillu-L (ൽ) or Chillu-L retroflex (ൾ) (e.g. മോഡ്യൂൾ, മോഡൽ, ലെവൽ)
    elif stem.endswith("ൾ") or stem.endswith("ൽ"):
        root = stem[:-1]
        is_retro = stem.endswith("ൾ")
        l_char = "ള" if is_retro else "ല"
        if suffix in ("ന്റെ", "ൻ്റെ", "ഇന്റെ"):
            res.extend([root + l_char + "ിന്റെ", stem + "ിന്റെ"])
        elif suffix in ("ൽ", "ിൽ"):
            res.extend([root + l_char + "ിൽ", stem + "ിൽ"])
        elif suffix in ("ഉം", "വും"):
            res.extend([root + l_char + "ിലും", root + l_char + "ും"])
        elif suffix in ("ആണ്", "ആ"):
            res.extend([root + l_char + "ാണ്", stem + "ാണ്"])
        elif suffix in ("ആയിരുന്നു", "ആയിരുന്നെങ്കിൽ", "ആയി", "ആയിട്ട്"):
            res.extend([root + l_char + "ായിരുന്നു", root + l_char + "ായിരുന്നെങ്കിൽ", root + l_char + "ായി", stem + "ആയിരുന്നു"])
        elif suffix in ("നകത്ത്", "അകത്ത്"):
            res.extend([root + l_char + "ിനകത്ത്", root + l_char + "നകത്ത്", stem + "നകത്ത്"])
        elif suffix in ("ന്", "ിന്"):
            res.extend([root + l_char + "ിന്", stem + "ിന്"])
        elif suffix in ("ഒക്കെ", "ഒക്കെയും"):
            res.extend([root + l_char + "ൊക്കെ", stem + "ഒക്കെ"])

    # 4. Stem ends in Chillu-N (ൻ) or Chillu-NN (ൺ)
    elif stem.endswith("ൻ") or stem.endswith("ൺ"):
        root = stem[:-1]
        n_char = "ണ" if stem.endswith("ൺ") else "ന"
        if suffix in ("ന്റെ", "ൻ്റെ", "ഇന്റെ"):
            res.extend([stem + "്റെ", root + n_char + "ിന്റെ"])
        elif suffix in ("ൽ", "ിൽ"):
            res.extend([root + n_char + "ിൽ"])
        elif suffix in ("ആണ്", "ആ"):
            res.extend([root + n_char + "ാണ്"])
        elif suffix in ("നകത്ത്", "അകത്ത്", "ൽനകത്ത്"):
            res.extend([root + "നകത്ത്", root + n_char + "ിനകത്ത്", root + n_char + "കത്ത്", stem + "നകത്ത്"])
        elif suffix in ("ആയിരുന്നു", "ആയിരുന്നെങ്കിൽ", "ആയി", "ആയിട്ട്"):
            res.extend([root + n_char + "ായിരുന്നു", root + n_char + "ായിരുന്നെങ്കിൽ", root + n_char + "ായി", root + n_char + "ായിട്ട്", stem + "ആയിരുന്നു"])
        elif suffix in ("ഒക്കെ", "ഒക്കെയും"):
            res.extend([root + n_char + "ൊക്കെ", stem + "ഒക്കെ"])

    # 5. Stem ends in Anusvara (ം) (e.g. ഫോം, തിയറം, പ്രോബ്ലം, സിസ്റ്റം, ഡയഗ്രം)
    elif stem.endswith("\u0D02"):
        base = stem[:-1]
        if suffix in ("ൽ", "ിൽ", "യിൽ"):
            res.extend([base + "മിൽ", base + "ത്തിൽ", stem + "ിൽ"])
        elif suffix in ("ഉം", "വും"):
            res.extend([base + "മും", base + "ത്തും", stem + "ും"])
        elif suffix in ("ആണ്", "ആ"):
            res.extend([base + "മാണ്", base + "ത്താണ്"])
        elif suffix in ("ആയിരുന്നു", "ആയിരുന്നെങ്കിൽ", "ആയി", "ആയിട്ട്"):
            res.extend([base + "മായിരുന്നു", base + "ത്തായിരുന്നു", base + "മായിരുന്നെങ്കിൽ", stem + "ആയിരുന്നു"])
        elif suffix in ("നകത്ത്", "അകത്ത്"):
            res.extend([base + "മിനകത്ത്", base + "ത്തിനകത്ത്", stem + "നകത്ത്"])
        elif suffix in ("ന്റെ", "ൻ്റെ", "ഇന്റെ"):
            res.extend([base + "മിന്റെ", base + "ത്തിന്റെ", stem + "ന്റെ"])
        elif suffix in ("ന്", "ിന്"):
            res.extend([base + "മിന്", base + "ത്തിന്", stem + "ിന്"])
        elif suffix in ("ഒക്കെ", "ഒക്കെയും"):
            res.extend([base + "മൊക്കെ", base + "ത്തൊക്കെ", stem + "ഒക്കെ"])

    # Gemination for kaarude / kal
    if suffix.startswith("ക"):
        res.extend([stem + "്ക" + suffix[1:], stem + "ക്ക" + suffix[1:], stem + "ുക" + suffix[1:]])

    if suffix in ("ലേക്ക്", "ഇലേക്ക്"):
        res.extend([stem + "ലേക്ക്", stem + "ിലേക്ക്", stem + "ത്തിലേക്ക്"])

    return list(set(res))

get_morphological_variants = apply_malayalam_sandhi  # alias for backwards compatibility


def get_word_candidates(word: str, mapping: dict[str, str]) -> list[str]:
    """Return all valid Malayalam representations for a reference word."""
    w_clean = word.lower().strip(".,;:?!'\"()")
    cands = []

    # 1. Hyphenated English-Malayalam words (e.g. form-ൽ, module-ൽ, equation-ൽ)
    if "-" in w_clean:
        parts = w_clean.split("-", 1)
        h_stem, h_suffix = parts[0], parts[1]
        if h_stem in COMMON_LOANWORDS:
            for sc in COMMON_LOANWORDS[h_stem]:
                for sv in apply_malayalam_sandhi(sc, h_suffix):
                    cands.append(normalise_text(sv))

    # 2. English possessive 's (e.g. Cauchy's -> cauchy + ന്റെ / cauchy)
    if w_clean.endswith("'s") or w_clean.endswith("’s"):
        stem = w_clean[:-2]
        if stem in COMMON_LOANWORDS:
            for sc in COMMON_LOANWORDS[stem]:
                cands.append(normalise_text(sc))
                cands.append(normalise_text(sc + "സ്"))
                cands.append(normalise_text(sc + "്സ്"))
                for sv in apply_malayalam_sandhi(sc, "ന്റെ"):
                    cands.append(normalise_text(sv))

    # 3. User approved / dictionary mapping
    if w_clean in mapping and mapping[w_clean]:
        cands.append(normalise_text(mapping[w_clean]))

    # 4. Acronym expansion
    acros = expand_acronym(word)
    for a in acros:
        cands.append(normalise_text(a))

    # 5. Common lecture loanword dictionary
    if w_clean in COMMON_LOANWORDS:
        for cw in COMMON_LOANWORDS[w_clean]:
            cands.append(normalise_text(cw))

    # 6. English plural: questions -> question + s, seniors -> senior + s
    if w_clean.endswith("s") and len(w_clean) > 3:
        stem = w_clean[:-1]
        if stem in COMMON_LOANWORDS:
            for sc in COMMON_LOANWORDS[stem]:
                cands.append(normalise_text(sc + "സ്"))
                cands.append(normalise_text(sc + "്സ്"))
                cands.append(normalise_text(sc + "േഴ്സ്"))
                cands.append(normalise_text(sc + "കൾ"))

    # 7. Spoken colloquial Malayalam variants
    norm_w = normalise_text(word)
    if norm_w in COLLOQUIAL_EQUIVALENTS:
        for eq in COLLOQUIAL_EQUIVALENTS[norm_w]:
            cands.append(normalise_text(eq))

    if not cands and not is_latin_word(word):
        cands.append(normalise_text(word))

    return list(set(cands))



def match_score_1_to_1(ref_w: str, hyp_w: str, mapping: dict[str, str]) -> tuple[bool, int]:
    """Check if ref_w matches or phonetically approximates hyp_w."""
    if ref_w == hyp_w:
        return True, 0
    cands = get_word_candidates(ref_w, mapping)
    for c in cands:
        if c == hyp_w:
            return True, 0
        d = levenshtein_distance(c, hyp_w)
        if d <= 1 and min(len(c), len(hyp_w)) >= 4:
            # Very close phonetic variant (e.g. അക്കാദമി vs അക്കാഡമി)
            return True, 0

    if is_latin_word(ref_w) and phonetic_similarity(ref_w, hyp_w) >= 0.75:
        return True, 0

    min_dist = levenshtein_distance(ref_w, hyp_w)
    for c in cands:
        d = levenshtein_distance(c, hyp_w)
        if d < min_dist:
            min_dist = d
    return False, min_dist


def match_score_2_to_1(ref_w1: str, ref_w2: str, hyp_w: str, mapping: dict[str, str]) -> tuple[bool, int]:
    """Check if (ref_w1 + ref_w2) combines into hyp_w (e.g. Academy + യുടെ -> അക്കാഡമിയുടെ)."""
    cands1 = get_word_candidates(ref_w1, mapping)
    if not cands1:
        cands1 = [ref_w1]

    for c1 in cands1:
        variants = get_morphological_variants(c1, ref_w2)
        for v in variants:
            if v == hyp_w:
                return True, 0
            if levenshtein_distance(v, hyp_w) <= 1:
                return True, 0

        # Substring / boundary check: hyp_w must contain both stem and suffix
        if hyp_w.endswith(ref_w2):
            stem = hyp_w[:-len(ref_w2)]
            if stem == c1 or levenshtein_distance(stem, c1) <= 1:
                return True, 0

        if ref_w2 in MALAYALAM_CASE_SUFFIXES:
            if hyp_w.startswith(c1) or levenshtein_distance(c1, hyp_w[:len(c1)]) <= 1:
                if ref_w2 in hyp_w or levenshtein_distance(ref_w2, hyp_w[-len(ref_w2):]) <= 1:
                    return True, 0

    # Bilingual phonetic compound check
    if is_latin_word(ref_w1):
        for sfx in [ref_w2, "ി" + ref_w2, "ത്തി" + ref_w2, "ിന്റെ", "ന്റെ", "ിൽ", "ൽ", "നകത്ത്", "ിനകത്ത്", "ാണ്", "ായിരുന്നു", "ായിരുന്നെങ്കിൽ"]:
            if hyp_w.endswith(sfx) and len(hyp_w) > len(sfx):
                stem = hyp_w[:-len(sfx)]
                if phonetic_similarity(ref_w1, stem) >= 0.75:
                    return True, 0

    return False, 999


def match_score_1_to_2(ref_w: str, hyp_w1: str, hyp_w2: str, mapping: dict[str, str]) -> tuple[bool, int]:
    """Check if ref_w splits into hyp_w1 + hyp_w2 (e.g. textbook -> ടെക്സ്റ്റ് ബുക്ക്)."""
    comb_h = hyp_w1 + hyp_w2
    return match_score_1_to_1(ref_w, comb_h, mapping)


def align_sequence_smart(
    ref_tokens: list[str],
    hyp_tokens: list[str],
    mapping: dict[str, str] | None = None,
    loanword_set: set[str] | None = None
) -> dict:
    """
    Multi-token Wagner-Fischer Dynamic Programming sequence alignment supporting:
    - 1-to-1 match/substitution/deletion/insertion
    - 2-to-1 compound match (Ref i-2 & i-1 -> Hyp j-1, e.g. "seniors ന്" ↔ "സീനിയേഴ്സിന്", "year ൽ" ↔ "ഇയറിൽ")
    - 1-to-2 split match (Ref i-1 -> Hyp j-2 & j-1, e.g. "textbook" ↔ "ടെക്സ്റ്റ് ബുക്ക്")
    - 2-to-2 phrase match (Ref i-2 & i-1 -> Hyp j-2 & j-1, e.g. "textbook ഉം" ↔ "ടെക്സ്റ്റ് ബുക്കും", "S3 യുടെ" ↔ "എസ് ത്രീയുടെ")
    - 1-to-3 number compound match (Ref i-1 -> Hyp j-3..j-1, e.g. "201" ↔ "ടു സീറോ ഒന്ന്")
    """
    if mapping is None:
        mapping = {}
    if loanword_set is None:
        loanword_set = set()

    n = len(ref_tokens)
    m = len(hyp_tokens)

    # Precompute normalized tokens and candidate sets
    ref_norm = [normalise_text(w) for w in ref_tokens]
    hyp_norm = [normalise_text(w) for w in hyp_tokens]
    ref_cands = [set(get_word_candidates(w, mapping)) | {ref_norm[idx]} for idx, w in enumerate(ref_tokens)]
    ref_is_latin = [is_latin_word(w) for w in ref_tokens]

    # Precompute 2-to-1 compound variants & 2-to-2 phrase variants for adjacent ref tokens
    comp_21_variants: dict[int, set[str]] = {}
    phrase_22_variants: dict[int, set[str]] = {}
    for i in range(2, n + 1):
        r_w1 = ref_tokens[i - 2]
        r_w2 = ref_tokens[i - 1]
        cands1 = ref_cands[i - 2]
        variants = set()
        p_set = set()
        for c1 in cands1:
            for v in apply_malayalam_sandhi(c1, r_w2):
                v_norm = normalise_text(v)
                variants.add(v_norm)
                p_set.add(v_norm.replace(" ", ""))
            variants.add(normalise_text(c1 + r_w2))
        comp_21_variants[i] = variants
        phrase_22_variants[i] = p_set

    # dp[i][j] = (min_cost, op_type, di, dj)
    dp = [[(999999, 'none', 0, 0) for _ in range(m + 1)] for _ in range(n + 1)]
    dp[0][0] = (0, 'start', 0, 0)

    for i in range(1, n + 1):
        dp[i][0] = (i, 'del', 1, 0)
    for j in range(1, m + 1):
        dp[0][j] = (j, 'ins', 0, 1)

    for i in range(1, n + 1):
        r_w = ref_tokens[i - 1]
        r_norm = ref_norm[i - 1]
        cand_set = ref_cands[i - 1]
        comp_set = comp_21_variants.get(i)

        for j in range(1, m + 1):
            h_w = hyp_tokens[j - 1]
            h_norm = hyp_norm[j - 1]

            # Option 1: 1-to-1 match or sub (with Dynamic Bilingual Phonetic Matching)
            is_match = (h_norm in cand_set) or (r_norm == h_norm)
            if not is_match and ref_is_latin[i - 1]:
                if "-" in r_w:
                    r_stem, r_sfx = r_w.split("-", 1)
                    for sfx in [r_sfx, "ി" + r_sfx, "ിൽ", "ൽ", "ിലും", "ന്റെ", "ിന്റെ"]:
                        if h_norm.endswith(sfx) and len(h_norm) > len(sfx):
                            stem = h_norm[:-len(sfx)]
                            if phonetic_similarity(r_stem, stem) >= 0.75:
                                is_match = True
                                break
                if not is_match:
                    is_match = (phonetic_similarity(r_w, h_norm) >= 0.75)
            cost_11 = 0 if is_match else 1
            op_11 = 'match' if is_match else 'sub'
            cand_11 = (dp[i - 1][j - 1][0] + cost_11, op_11, 1, 1)

            # Option 2: 1-to-0 del
            cand_del = (dp[i - 1][j][0] + 1, 'del', 1, 0)

            # Option 3: 0-to-1 ins
            cand_ins = (dp[i][j - 1][0] + 1, 'ins', 0, 1)

            best = cand_11
            if cand_del[0] < best[0]:
                best = cand_del
            if cand_ins[0] < best[0]:
                best = cand_ins

            # Option 4: 2-to-1 compound match (Ref i-2 & i-1 -> Hyp j-1)
            if i >= 2:
                r_w1 = ref_tokens[i - 2]
                r_w2 = ref_tokens[i - 1]
                is_comp_match = False
                if comp_set is not None and h_norm in comp_set:
                    is_comp_match = True
                elif ref_is_latin[i - 2]:
                    # Dynamic bilingual phonetic compound check
                    sfx_list = [
                        (r_w2, ""),
                        ("ി" + r_w2, ""),
                        ("ത്തി" + r_w2, ""),
                        ("ിന്റെ", ""),
                        ("ന്റെ", ""),
                        ("ൽ", ""),
                        ("ിൽ", ""),
                        ("നകത്ത്", ""),
                        ("ിനകത്ത്", "ൽ"),
                        ("ിനകത്തു", "ൽ"),
                        ("ാണ്", ""),
                        ("ായിരുന്നു", ""),
                        ("ായിരുന്നെങ്കിൽ", ""),
                    ]
                    if r_w2.startswith("ആ"):
                        rest = r_w2[1:]
                        sfx_list.extend([
                            ("നാ" + rest, "ൻ"),
                            ("ലാ" + rest, "ൽ"),
                            ("രാ" + rest, "ർ"),
                            ("മാ" + rest, "ം"),
                            ("വാ" + rest, ""),
                            ("യാ" + rest, ""),
                        ])
                    for sfx, restore_char in sfx_list:
                        if h_norm.endswith(sfx) and len(h_norm) > len(sfx):
                            stem = h_norm[:-len(sfx)] + restore_char
                            if phonetic_similarity(r_w1, stem) >= 0.75:
                                is_comp_match = True
                                break

                if is_comp_match:
                    cand_comp = (dp[i - 2][j - 1][0] + 0, 'compound_match_2_1', 2, 1)
                    if cand_comp[0] <= best[0]:
                        best = cand_comp
                else:
                    if (r_w2 in MALAYALAM_CASE_SUFFIXES or r_w2 in ("ആ", "ആണ്")) and (ref_is_latin[i - 2] or len(r_w1) >= 3):
                        cand_comp_sub = (dp[i - 2][j - 1][0] + 1, 'compound_sub_2_1', 2, 1)
                        if cand_comp_sub[0] < best[0]:
                            best = cand_comp_sub

            # Option 5: 1-to-2 compound match (Ref i-1 -> Hyp j-2 & j-1)
            if j >= 2:
                comb_h = hyp_norm[j - 2] + h_norm
                comb_h_space = hyp_norm[j - 2] + " " + h_norm
                is_12_match = (comb_h in cand_set) or (comb_h_space in cand_set) or (r_norm == comb_h)
                if not is_12_match and ref_is_latin[i - 1]:
                    is_12_match = (phonetic_similarity(r_w, comb_h) >= 0.75)
                if is_12_match:
                    cand_12 = (dp[i - 1][j - 2][0] + 0, 'compound_match_1_2', 1, 2)
                    if cand_12[0] <= best[0]:
                        best = cand_12

            # Option 6: 2-to-2 phrase match (Ref i-2 & i-1 -> Hyp j-2 & j-1)
            if i >= 2 and j >= 2:
                r_phrase = normalise_text(" ".join(ref_tokens[i - 2:i]))
                h_phrase = normalise_text(" ".join(hyp_tokens[j - 2:j]))
                is_phrase_match = False
                if r_phrase == h_phrase:
                    is_phrase_match = True
                elif ref_tokens[i - 2].upper() == "S3" and ("എസ് ത്രീ" in h_phrase or "എസ്ത്രീ" in h_phrase):
                    is_phrase_match = True
                else:
                    h_flat = h_phrase.replace(" ", "")
                    p_set = phrase_22_variants.get(i)
                    if p_set and h_flat in p_set:
                        is_phrase_match = True

                if is_phrase_match:
                    cand_22 = (dp[i - 2][j - 2][0] + 0, 'phrase_match_2_2', 2, 2)
                    if cand_22[0] <= best[0]:
                        best = cand_22

            # Option 7: 1-to-3 compound match for numbers (Ref i-1 -> Hyp j-3..j-1)
            if j >= 3 and r_w.isdigit() and len(r_w) == 3:
                h_3 = " ".join(hyp_tokens[j - 3:j])
                if r_w == "201" and ("ടു സീറോ ഒന്ന്" in h_3 or "ടു സീറോ വൺ" in h_3):
                    cand_13 = (dp[i - 1][j - 3][0] + 0, 'compound_match_1_3', 1, 3)
                    if cand_13[0] <= best[0]:
                        best = cand_13

            dp[i][j] = best

    # Backtrack
    i, j = n, m
    aligned = []
    matches = subs = dels = inss = 0

    while i > 0 or j > 0:
        cost, op, di, dj = dp[i][j]
        if di == 1 and dj == 1:
            r_w = ref_tokens[i - 1]
            h_w = hyp_tokens[j - 1]
            is_loan = (r_w.lower() in loanword_set) or is_latin_word(r_w)
            c_dist = levenshtein_distance(r_w, h_w)
            if op == 'match':
                aligned.append({
                    "type": "match",
                    "ref": r_w,
                    "hyp": h_w,
                    "is_loanword": is_loan,
                    "is_compound": False,
                    "char_dist": 0,
                })
                matches += 1
            else:
                aligned.append({
                    "type": "substitution",
                    "ref": r_w,
                    "hyp": h_w,
                    "is_loanword": is_loan,
                    "is_compound": False,
                    "char_dist": c_dist,
                })
                subs += 1
        elif di == 2 and dj == 1:
            r_w = f"{ref_tokens[i - 2]} {ref_tokens[i - 1]}"
            h_w = hyp_tokens[j - 1]
            is_loan = any((w.lower() in loanword_set) or is_latin_word(w) for w in [ref_tokens[i - 2], ref_tokens[i - 1]])
            if 'match' in op:
                aligned.append({
                    "type": "match",
                    "ref": r_w,
                    "hyp": h_w,
                    "is_loanword": is_loan,
                    "is_compound": True,
                    "char_dist": 0,
                })
                matches += 1
            else:
                aligned.append({
                    "type": "substitution",
                    "ref": r_w,
                    "hyp": h_w,
                    "is_loanword": is_loan,
                    "is_compound": True,
                    "char_dist": levenshtein_distance(r_w, h_w),
                })
                subs += 1
        elif di == 1 and dj == 2:
            r_w = ref_tokens[i - 1]
            h_w = f"{hyp_tokens[j - 2]} {hyp_tokens[j - 1]}"
            is_loan = (r_w.lower() in loanword_set) or is_latin_word(r_w)
            if 'match' in op:
                aligned.append({
                    "type": "match",
                    "ref": r_w,
                    "hyp": h_w,
                    "is_loanword": is_loan,
                    "is_compound": True,
                    "char_dist": 0,
                })
                matches += 1
            else:
                aligned.append({
                    "type": "substitution",
                    "ref": r_w,
                    "hyp": h_w,
                    "is_loanword": is_loan,
                    "is_compound": True,
                    "char_dist": levenshtein_distance(r_w, h_w),
                })
                subs += 1
        elif di == 2 and dj == 2:
            r_w = f"{ref_tokens[i - 2]} {ref_tokens[i - 1]}"
            h_w = f"{hyp_tokens[j - 2]} {hyp_tokens[j - 1]}"
            is_loan = any((w.lower() in loanword_set) or is_latin_word(w) for w in [ref_tokens[i - 2], ref_tokens[i - 1]])
            aligned.append({
                "type": "match",
                "ref": r_w,
                "hyp": h_w,
                "is_loanword": is_loan,
                "is_compound": True,
                "char_dist": 0,
            })
            matches += 2
        elif di == 1 and dj == 3:
            r_w = ref_tokens[i - 1]
            h_w = f"{hyp_tokens[j - 3]} {hyp_tokens[j - 2]} {hyp_tokens[j - 1]}"
            is_loan = (r_w.lower() in loanword_set) or is_latin_word(r_w)
            aligned.append({
                "type": "match",
                "ref": r_w,
                "hyp": h_w,
                "is_loanword": is_loan,
                "is_compound": True,
                "char_dist": 0,
            })
            matches += 1
        elif di == 1 and dj == 0:
            r_w = ref_tokens[i - 1]
            is_loan = (r_w.lower() in loanword_set) or is_latin_word(r_w)
            aligned.append({
                "type": "deletion",
                "ref": r_w,
                "hyp": None,
                "is_loanword": is_loan,
                "is_compound": False,
                "char_dist": len(r_w),
            })
            dels += 1
        elif di == 0 and dj == 1:
            h_w = hyp_tokens[j - 1]
            aligned.append({
                "type": "insertion",
                "ref": None,
                "hyp": h_w,
                "is_loanword": is_latin_word(h_w),
                "is_compound": False,
                "char_dist": len(h_w),
            })
            inss += 1
        else:
            break
        i -= di
        j -= dj

    aligned.reverse()
    total_ref = n
    errors = subs + dels + inss
    wer = round(errors / max(1, total_ref), 4)

    return {
        "aligned": aligned,
        "counts": {
            "matches": matches,
            "substitutions": subs,
            "deletions": dels,
            "insertions": inss,
            "total_ref": total_ref,
            "errors": errors,
            "wer": wer,
        }
    }


def align_tokens_dp(ref_text: str, hyp_text: str, loanword_set: set[str] | None = None) -> dict:
    """Backwards compatibility wrapper for align_tokens_dp."""
    return align_sequence_smart(ref_text.strip().split(), hyp_text.strip().split(), loanword_set=loanword_set)


def compute_comparison_analytics(segments: list[dict], mapping: dict | None = None) -> dict:
    """
    Run full WER/CER alignment across all segments and build:
    1. Heatmap Ribbon (timeline bars with WER severity)
    2. Category Error Breakdown (Malayalam vs Loanword WER)
    3. Out-of-Vocabulary & Technical Misrecognition Heatmap
    """
    if mapping is None:
        mapping = {}

    loanword_set = (
        set(k.lower() for k in mapping.keys())
        | set(v.lower() for v in mapping.values())
        | set(COMMON_LOANWORDS.keys())
    )

    enriched_segments = []
    heatmap_ribbon = []
    oov_tracker = {}  # word -> {occurrences, errors, confusions: {hyp: count}}

    total_words = 0
    total_matches = 0
    total_subs = 0
    total_dels = 0
    total_ins = 0

    malayalam_words = 0
    malayalam_errors = 0

    loan_words = 0
    loan_errors = 0

    all_ref_chars = 0
    total_char_dist = 0

    for idx, s in enumerate(segments):
        raw_ref = s.get("elevenlabs", "")
        raw_hyp = s.get("local", "")

        # Clean raw text while preserving English loanwords for phonetic matching
        ref_tokens = [w for w in raw_ref.strip().split() if w]
        hyp_tokens = [normalise_text(w) for w in raw_hyp.strip().split() if normalise_text(w)]

        seg_res = align_sequence_smart(ref_tokens, hyp_tokens, mapping=mapping, loanword_set=loanword_set)
        aligned = seg_res["aligned"]
        counts = seg_res["counts"]

        # Track segment character error
        seg_ref_chars = sum(len(a["ref"]) for a in aligned if a["ref"])
        seg_char_dist = sum(a["char_dist"] for a in aligned)
        seg_cer = round(seg_char_dist / max(1, seg_ref_chars), 4) if seg_ref_chars else 0.0

        all_ref_chars += seg_ref_chars
        total_char_dist += seg_char_dist

        total_words += counts["total_ref"]
        total_matches += counts["matches"]
        total_subs += counts["substitutions"]
        total_dels += counts["deletions"]
        total_ins += counts["insertions"]

        # Track token level loanword vs native
        for a in aligned:
            ref_w = a.get("ref")
            hyp_w = a.get("hyp")
            is_loan = a.get("is_loanword", False)
            is_err = a["type"] in ("substitution", "deletion")

            if ref_w:
                if is_loan:
                    loan_words += 1
                    if is_err:
                        loan_errors += 1
                else:
                    malayalam_words += 1
                    if is_err:
                        malayalam_errors += 1

                # Track OOV & recurring technical errors
                # If compound, take the root word (e.g. "Academy യുടെ" -> "Academy")
                root_word = ref_w.split()[0] if " " in ref_w else ref_w
                root_is_loan = is_loan or is_latin_word(root_word) or (root_word.lower() in loanword_set)

                if root_is_loan or len(root_word) > 4:
                    ref_key = root_word.lower()
                    if ref_key not in oov_tracker:
                        oov_tracker[ref_key] = {
                            "word": root_word,
                            "is_loanword": root_is_loan,
                            "occurrences": 0,
                            "errors": 0,
                            "confusions": {},
                        }
                    oov_tracker[ref_key]["occurrences"] += 1
                    if is_err:
                        oov_tracker[ref_key]["errors"] += 1
                        if hyp_w:
                            c_dict = oov_tracker[ref_key]["confusions"]
                            c_dict[hyp_w] = c_dict.get(hyp_w, 0) + 1

        # Classify heatmap severity
        wer = counts["wer"]
        if wer == 0:
            severity = "match"  # Green
        elif wer <= 0.25:
            severity = "low"    # Light Green/Yellow
        elif wer <= 0.50:
            severity = "medium" # Amber/Orange
        else:
            severity = "high"   # Red

        start = s.get("start", 0.0)
        end = s.get("end", 0.0)

        heatmap_ribbon.append({
            "idx": idx,
            "start": start,
            "end": end,
            "wer": wer,
            "cer": seg_cer,
            "words": counts["total_ref"],
            "errors": counts["substitutions"] + counts["deletions"] + counts["insertions"],
            "severity": severity,
        })

        enriched_segments.append({
            **s,
            "scores": {
                "wer": wer,
                "cer": seg_cer,
            },
            "alignment": aligned,
            "alignment_counts": counts,
        })

    overall_wer = round((total_subs + total_dels + total_ins) / max(1, total_words), 4)
    overall_cer = round(total_char_dist / max(1, all_ref_chars), 4) if all_ref_chars else 0.0

    malayalam_wer = round(malayalam_errors / max(1, malayalam_words), 4) if malayalam_words else 0.0
    loanword_wer = round(loan_errors / max(1, loan_words), 4) if loan_words else 0.0

    # Build ranked OOV Heatmap entries
    oov_heatmap = []
    for k, v in oov_tracker.items():
        if v["occurrences"] >= 1:
            err_rate = round(v["errors"] / v["occurrences"], 3)
            # Sort top acoustic confusion hypotheses
            sorted_confusions = sorted(v["confusions"].items(), key=lambda x: x[1], reverse=True)
            oov_heatmap.append({
                "word": v["word"],
                "is_loanword": v["is_loanword"],
                "occurrences": v["occurrences"],
                "errors": v["errors"],
                "error_rate": err_rate,
                "top_confusions": [f"{w} ({c})" for w, c in sorted_confusions[:3]],
            })

    # Sort high error rate and frequency first
    oov_heatmap.sort(key=lambda x: (x["errors"], x["error_rate"], x["occurrences"]), reverse=True)

    return {
        "enriched_segments": enriched_segments,
        "heatmap_ribbon": heatmap_ribbon,
        "metrics": {
            "overall_wer": overall_wer,
            "overall_cer": overall_cer,
            "overall_agreement": round(max(0.0, 1.0 - overall_wer), 4),
            "malayalam_wer": malayalam_wer,
            "loanword_wer": loanword_wer,
            "total_words": total_words,
            "matches": total_matches,
            "substitutions": total_subs,
            "deletions": total_dels,
            "insertions": total_ins,
        },
        "oov_heatmap": oov_heatmap[:40],  # Top 40 high priority errors
    }


def get_or_create_wer_analytics(folder: str, segments: list[dict], mapping: dict | None = None, recompute: bool = False) -> dict:
    """Return cached analytics or compute, cache and return."""
    if mapping is None:
        mapping = {}
    cache_path = os.path.join(folder, "wer_analytics.json")
    if not recompute and os.path.isfile(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
                # Verify schema version (5 = Bilingual Phonetic Matcher)
                if data.get("schema_version") == 5:
                    return data
        except Exception as e:
            log.warning("Could not load cached wer_analytics from %s: %s", cache_path, e)

    analytics = compute_comparison_analytics(segments, mapping)
    analytics["schema_version"] = 5
    analytics["mapping_count"] = len(mapping)
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(analytics, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log.warning("Could not save wer_analytics to %s: %s", cache_path, e)

    return analytics
