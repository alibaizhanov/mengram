"""
Conversation Extractor v2 — extracts RICH knowledge from conversations.

Extracts:
1. Entities (person, project, technology, company, concept)
2. Facts — short assertions
3. Relations — connections between entities
4. Knowledge — solutions, formulas, recipes, configs, commands (with artifacts)

Knowledge is the killer feature. LLM determines the knowledge type:
  [solution] — problem solution (code, config)
  [formula] — formula, equation
  [treatment] — treatment, prescription
  [experiment] — experiment result
  [recipe] — recipe (cooking, process)
  [decision] — decision made
  [command] — useful command / instruction
  [reference] — link, source
  [insight] — observation, insight
  [example] — example, case
  ... any other type that fits the context
"""

import os
import sys
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from engine.extractor.llm_client import LLMClient

_logger = logging.getLogger("mengram")

EXTRACTION_PROMPT_VERSION = os.environ.get("EXTRACTION_PROMPT_VERSION", "v1")

# OpenAI Structured Outputs schema — guarantees valid JSON with correct types
EXTRACTION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "memory_extraction",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string", "enum": ["person", "project", "technology", "company", "concept", "place", "activity"]},
                            "facts": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "fact": {"type": "string"},
                                        "when": {"type": ["string", "null"]}
                                    },
                                    "required": ["fact", "when"],
                                    "additionalProperties": False
                                }
                            }
                        },
                        "required": ["name", "type", "facts"],
                        "additionalProperties": False
                    }
                },
                "relations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "from": {"type": "string"},
                            "to": {"type": "string"},
                            "type": {"type": "string"},
                            "description": {"type": "string"}
                        },
                        "required": ["from", "to", "type", "description"],
                        "additionalProperties": False
                    }
                },
                "knowledge": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "entity": {"type": "string"},
                            "type": {"type": "string", "enum": ["solution", "formula", "command", "insight", "decision", "recipe", "reference"]},
                            "title": {"type": "string"},
                            "content": {"type": "string"},
                            "artifact": {"type": ["string", "null"]}
                        },
                        "required": ["entity", "type", "title", "content", "artifact"],
                        "additionalProperties": False
                    }
                },
                "episodes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string"},
                            "context": {"type": "string"},
                            "outcome": {"type": "string"},
                            "participants": {"type": "array", "items": {"type": "string"}},
                            "emotional_valence": {"type": "string", "enum": ["positive", "negative", "neutral", "mixed"]},
                            "importance": {"type": "number"},
                            "happened_at": {"type": ["string", "null"]}
                        },
                        "required": ["summary", "context", "outcome", "participants", "emotional_valence", "importance", "happened_at"],
                        "additionalProperties": False
                    }
                },
                "procedures": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "trigger": {"type": "string"},
                            "steps": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "step": {"type": "integer"},
                                        "action": {"type": "string"},
                                        "detail": {"type": "string"}
                                    },
                                    "required": ["step", "action", "detail"],
                                    "additionalProperties": False
                                }
                            },
                            "entities": {"type": "array", "items": {"type": "string"}}
                        },
                        "required": ["name", "trigger", "steps", "entities"],
                        "additionalProperties": False
                    }
                }
            },
            "required": ["entities", "relations", "knowledge", "episodes", "procedures"],
            "additionalProperties": False
        }
    }
}



def _ensure_str(val, fallback=""):
    """Coerce LLM output to string. Handles nested dicts gracefully."""
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        for key in ("text", "fact", "content", "value", "name", "description"):
            if key in val and isinstance(val[key], str):
                _logger.debug(f"Coerced dict to str via key '{key}': {val}")
                return val[key]
        _logger.warning(f"⚠️ LLM returned dict where str expected, using str(): {val}")
        return str(val)
    if val is None:
        return fallback
    _logger.debug(f"Coerced {type(val).__name__} to str: {val}")
    return str(val)


EXTRACTION_PROMPT = """You are a knowledge extraction system. Extract personal knowledge from ALL speakers in the conversation.

Return ONLY valid JSON without markdown.

WHO TO EXTRACT ABOUT:
- Extract facts about ALL people mentioned — both speakers share equally important information
- Extract identity, preferences, activities, relationships, plans, events for every person
- If someone says "I went to a support group" — extract it as a fact about THAT person
- DO NOT extract: generic knowledge the AI assistant explained (unless a person confirmed they use it)
- If a person says "I"/"me"/"my" — resolve to their name if known, otherwise "User"
{existing_context}
ENTITY RULES:
- Named entities with 1+ extractable facts (people, places, organizations, activities, projects)
- entity_type: person, project, technology, company, concept, place, activity
- Create separate entities for each person by name
- Single-fact entities are OK if the fact is important (identity, job, location, hobby)

ENTITY NAMING:
- EXACT casing from context: "Mengram" not "MENGRAM", "PostgreSQL" not "postgresql"
- FULL name when known: "Ali Baizhanov" not "Ali", "Uzum Bank" not "uzum"
- If entity already exists above — use EXACT SAME NAME (do not create duplicates)

FACT RULES:
- Concise but complete, under 20 words
- ALWAYS include dates/times when mentioned or inferrable from context
  GOOD: "attended LGBTQ support group on May 7, 2023"
  GOOD: "started pottery class in June 2023"
  GOOD: "works as a software engineer at Google"
  BAD: "attended support group" (date was available but omitted!)
- If the conversation includes timestamps like "[1:56 pm on 8 May, 2023]" and someone says "yesterday" — calculate the actual date and include it
- ONLY facts that DIRECTLY describe the entity they're assigned to
- Keep project facts on projects, personal facts on the person — don't mix
- DO NOT extract: meta-conversation actions ("asked a question", "sent a message", "said hello")
- Facts can optionally include a "when" date field (see format below)

FACT DEDUP — check existing facts above. Do NOT re-extract facts that already exist (even if worded slightly differently).
If someone says "I use Python" and existing context already has "uses Python" → skip it.
If someone says "I switched from React to Svelte" and existing has "uses React" → extract "switched to Svelte" (this is NEW info).

EPISODIC MEMORY — extract events and interactions:
- An episode = something that HAPPENED: an activity, decision, milestone, trip, class, meetup, achievement
- Extract any event worth remembering — err on the side of inclusion
- Include: what happened (summary), details (context), result (outcome), who was involved (participants)
- Include "happened_at" date if known from conversation context (e.g. "2023-05-07")
- emotional_valence: positive, negative, neutral, mixed
- importance: 0.3 (minor) to 0.9 (major milestone)
- Do NOT create episodes for pure greetings or small talk with no content

PROCEDURAL MEMORY — extract workflows/processes:
- A procedure = a repeatable sequence of steps someone performs or described
- Only extract if there are 2+ concrete steps forming a workflow
- Include: name, trigger (when to use it), steps (ordered actions)
- Link to entities involved
- Extract from IMPLICIT workflows too ("first I do X, then Y, then Z")

Response format (strict JSON, no ```):
{{
  "entities": [
    {{
      "name": "Entity Name",
      "type": "person|project|technology|company|concept|place|activity",
      "facts": [
        {{"fact": "simple fact as string", "when": null}},
        {{"fact": "fact with date", "when": "2023-05-07"}}
      ]
    }}
  ],
  "relations": [
    {{
      "from": "Entity 1",
      "to": "Entity 2",
      "type": "works_at|uses|member_of|related_to|depends_on|created_by|friend_of|lives_in",
      "description": "short description"
    }}
  ],
  "knowledge": [
    {{
      "entity": "Entity this knowledge belongs to",
      "type": "solution|formula|command|insight|decision|recipe|reference",
      "title": "Short descriptive title",
      "content": "Detailed explanation",
      "artifact": "code/config/formula/command (optional, null if none)"
    }}
  ],
  "episodes": [
    {{
      "summary": "Brief description of what happened (under 20 words)",
      "context": "Detailed description of the event",
      "outcome": "What was decided, resolved, or resulted",
      "participants": ["Entity1", "Entity2"],
      "emotional_valence": "positive|negative|neutral|mixed",
      "importance": 0.5,
      "happened_at": "2023-05-07 or null if unknown"
    }}
  ],
  "procedures": [
    {{
      "name": "Short procedure name",
      "trigger": "When/why to use this procedure",
      "steps": [
        {{"step": 1, "action": "What to do", "detail": "Specific instruction"}},
        {{"step": 2, "action": "Next step", "detail": "Specifics"}}
      ],
      "entities": ["Entity1"]
    }}
  ]
}}

EXAMPLE:
Input conversation:
  User: "[2023-06-15] Ali: I deployed mengram on Railway yesterday, everything works."
  Assistant: "[2023-06-15] Bot: Great! Which PostgreSQL version?"
  User: "[2023-06-15] Ali: 15, hosted on Supabase."

Output:
{{
  "entities": [
    {{"name": "Ali", "type": "person", "facts": [
      {{"fact": "deployed Mengram on Railway", "when": "2023-06-14"}},
      {{"fact": "uses Supabase with PostgreSQL 15", "when": null}}
    ]}},
    {{"name": "Mengram", "type": "project", "facts": [
      {{"fact": "deployed on Railway", "when": "2023-06-14"}},
      {{"fact": "uses Supabase PostgreSQL 15", "when": null}}
    ]}}
  ],
  "relations": [
    {{"from": "Ali", "to": "Mengram", "type": "created_by", "description": "deployed and manages"}},
    {{"from": "Mengram", "to": "Supabase", "type": "depends_on", "description": "database hosting"}}
  ],
  "knowledge": [],
  "episodes": [
    {{
      "summary": "Ali deployed Mengram on Railway successfully",
      "context": "Deployed Mengram to Railway with Supabase PostgreSQL 15.",
      "outcome": "Deployment successful",
      "participants": ["Ali", "Mengram", "Railway"],
      "emotional_valence": "positive",
      "importance": 0.7,
      "happened_at": "2023-06-14"
    }}
  ],
  "procedures": []
}}

CONVERSATION:
{conversation}

Extract knowledge (return ONLY JSON):"""


EXISTING_CONTEXT_BLOCK = """
EXISTING ENTITIES FOR THIS USER (use same names, avoid duplicate facts):
{context}
"""


# ============================================================
# V2 — stricter filtering, better attribution, quality bar
# Activated via EXTRACTION_PROMPT_VERSION=v2 env var
# ============================================================

EXTRACTION_PROMPT_V2 = """You are a knowledge extraction system. Extract personal knowledge from the User's perspective.

Return ONLY valid JSON without markdown.

WHO TO EXTRACT ABOUT:
- Extract facts about the User and real people/entities the User mentions
- The User's own statements ("I work at Google", "my dog is named Rex") → facts about the User
- The Assistant's research, explanations, and general knowledge are NOT facts about anyone — skip them
- ONLY extract from Assistant content if the User EXPLICITLY CONFIRMS it applies to them
  Example: Assistant says "React uses virtual DOM" → DO NOT extract
  Example: User says "yeah I use React with virtual DOM daily" → extract for User
- If someone says "I"/"me"/"my" — that's the User (resolve to their name if known, otherwise "User")
- Information from web searches, docs, tool outputs, or code analysis is NOT a personal fact
{existing_context}
DO NOT EXTRACT — meta-conversation noise:
- Assistant's internal actions: "let me search", "I'll look into", "here's what I found"
- Tool/function call outputs, search results, code analysis by the Assistant
- Procedural filler: "Sure, I can help", "Let me explain", "Here's an overview"
- The User asking the Assistant to do something ("can you search for X", "help me with Y")
  — unless it reveals a personal fact about the User (e.g. "search for flights to Tokyo" → User plans to visit Tokyo)
- Any fact that describes what happened IN this conversation rather than about the real world

ENTITY RULES:
- Named entities with 1+ extractable facts (people, places, organizations, activities, projects)
- entity_type: person, project, technology, company, concept, place, activity
- Create separate entities for each person by name
- Single-fact entities are OK if the fact is important (identity, job, location, hobby)

ENTITY NAMING:
- EXACT casing from context: "Mengram" not "MENGRAM", "PostgreSQL" not "postgresql"
- FULL name when known: "Ali Baizhanov" not "Ali", "Uzum Bank" not "uzum"
- If entity already exists above — use EXACT SAME NAME (do not create duplicates)

FACT RULES:
- Concise but complete, under 20 words
- ALWAYS include dates/times when mentioned or inferrable from context
  GOOD: "attended LGBTQ support group on May 7, 2023"
  GOOD: "started pottery class in June 2023"
  GOOD: "works as a software engineer at Google"
  BAD: "attended support group" (date was available but omitted!)
- If the conversation includes timestamps like "[1:56 pm on 8 May, 2023]" and someone says "yesterday" — calculate the actual date and include it
- ONLY facts that DIRECTLY describe the entity they're assigned to
- Keep project facts on projects, personal facts on the person — don't mix
- Facts can optionally include a "when" date field (see format below)

QUALITY BAR — only extract facts worth recalling in a FUTURE conversation:
- YES: identity, preferences, skills, relationships, plans, locations, tools used
- YES: decisions made, problems solved, opinions expressed
- NO: transient actions ("searched for X", "looked at Y", "opened the file")
- NO: conversation mechanics ("thanked the assistant", "asked for help")
- NO: generic knowledge anyone could look up ("Python is a programming language")
- When in doubt about ASSISTANT content, skip it. Under-extraction of assistant noise is better than junk.
- BUT: the User's direct "I"/"my" statements are ALWAYS worth extracting — NEVER skip them.
  "I already use pytest at work" → MUST extract (uses pytest, works somewhere)
  "I have a side project" → MUST extract

FACT DEDUP — check existing facts above. Do NOT re-extract facts that already exist (even if worded slightly differently).
If someone says "I use Python" and existing context already has "uses Python" → skip it.
If someone says "I switched from React to Svelte" and existing has "uses React" → extract "switched to Svelte" (this is NEW info).

EPISODIC MEMORY — extract events and interactions:
- An episode = something that HAPPENED: an activity, decision, milestone, trip, class, meetup, achievement
- Only extract events from the REAL WORLD — not meta-conversation events
- Include: what happened (summary), details (context), result (outcome), who was involved (participants)
- Include "happened_at" date if known from conversation context (e.g. "2023-05-07")
- emotional_valence: positive, negative, neutral, mixed
- importance: 0.3 (minor) to 0.9 (major milestone)
- Do NOT create episodes for pure greetings, small talk, or "User asked Assistant to do X"

PROCEDURAL MEMORY — extract workflows/processes:
- A procedure = a repeatable sequence of steps someone performs or described
- Only extract if there are 2+ concrete steps forming a workflow
- Include: name, trigger (when to use it), steps (ordered actions)
- Link to entities involved
- Extract from IMPLICIT workflows too ("first I do X, then Y, then Z")

Response format (strict JSON, no ```):
{{
  "entities": [
    {{
      "name": "Entity Name",
      "type": "person|project|technology|company|concept|place|activity",
      "facts": [
        {{"fact": "simple fact as string", "when": null}},
        {{"fact": "fact with date", "when": "2023-05-07"}}
      ]
    }}
  ],
  "relations": [
    {{
      "from": "Entity 1",
      "to": "Entity 2",
      "type": "works_at|uses|member_of|related_to|depends_on|created_by|friend_of|lives_in",
      "description": "short description"
    }}
  ],
  "knowledge": [
    {{
      "entity": "Entity this knowledge belongs to",
      "type": "solution|formula|command|insight|decision|recipe|reference",
      "title": "Short descriptive title",
      "content": "Detailed explanation",
      "artifact": "code/config/formula/command (optional, null if none)"
    }}
  ],
  "episodes": [
    {{
      "summary": "Brief description of what happened (under 20 words)",
      "context": "Detailed description of the event",
      "outcome": "What was decided, resolved, or resulted",
      "participants": ["Entity1", "Entity2"],
      "emotional_valence": "positive|negative|neutral|mixed",
      "importance": 0.5,
      "happened_at": "2023-05-07 or null if unknown"
    }}
  ],
  "procedures": [
    {{
      "name": "Short procedure name",
      "trigger": "When/why to use this procedure",
      "steps": [
        {{"step": 1, "action": "What to do", "detail": "Specific instruction"}},
        {{"step": 2, "action": "Next step", "detail": "Specifics"}}
      ],
      "entities": ["Entity1"]
    }}
  ]
}}

EXAMPLE:
Input conversation:
  User: "[2023-06-15] Ali: I deployed mengram on Railway yesterday, everything works."
  Assistant: "[2023-06-15] Bot: Great! Which PostgreSQL version?"
  User: "[2023-06-15] Ali: 15, hosted on Supabase."

Output:
{{
  "entities": [
    {{"name": "Ali", "type": "person", "facts": [
      {{"fact": "deployed Mengram on Railway", "when": "2023-06-14"}},
      {{"fact": "uses Supabase with PostgreSQL 15", "when": null}}
    ]}},
    {{"name": "Mengram", "type": "project", "facts": [
      {{"fact": "deployed on Railway", "when": "2023-06-14"}},
      {{"fact": "uses Supabase PostgreSQL 15", "when": null}}
    ]}}
  ],
  "relations": [
    {{"from": "Ali", "to": "Mengram", "type": "created_by", "description": "deployed and manages"}},
    {{"from": "Mengram", "to": "Supabase", "type": "depends_on", "description": "database hosting"}}
  ],
  "knowledge": [],
  "episodes": [
    {{
      "summary": "Ali deployed Mengram on Railway successfully",
      "context": "Deployed Mengram to Railway with Supabase PostgreSQL 15.",
      "outcome": "Deployment successful",
      "participants": ["Ali", "Mengram", "Railway"],
      "emotional_valence": "positive",
      "importance": 0.7,
      "happened_at": "2023-06-14"
    }}
  ],
  "procedures": []
}}

CONVERSATION:
{conversation}

Extract knowledge (return ONLY JSON):"""


@dataclass
class ExtractedFact:
    """Extracted fact with optional temporal metadata."""
    content: str
    event_date: Optional[str] = None  # e.g. "2023-05-07"


@dataclass
class ExtractedEntity:
    """Extracted entity"""
    name: str
    entity_type: str  # person, project, technology, company, concept, place, activity
    facts: list[ExtractedFact] = field(default_factory=list)

    def __repr__(self):
        return f"Entity({self.entity_type}: {self.name}, facts={len(self.facts)})"


@dataclass
class ExtractedRelation:
    """Extracted relation"""
    from_entity: str
    to_entity: str
    relation_type: str
    description: str = ""

    def __repr__(self):
        return f"Relation({self.from_entity} --{self.relation_type}--> {self.to_entity})"


@dataclass
class ExtractedKnowledge:
    """Extracted knowledge — solution, formula, command, etc."""
    entity: str           # which entity this belongs to
    knowledge_type: str   # solution, formula, treatment, command, insight, ...
    title: str            # short title
    content: str          # detailed description
    artifact: Optional[str] = None  # code, config, formula, command

    def __repr__(self):
        has_artifact = "📎" if self.artifact else ""
        return f"Knowledge([{self.knowledge_type}] {self.title} → {self.entity} {has_artifact})"


@dataclass
class ExtractedEpisode:
    """Extracted episode — specific event, interaction."""
    summary: str                  # short description (up to 20 words)
    context: str = ""             # detailed description
    outcome: str = ""             # result/outcome
    participants: list[str] = field(default_factory=list)  # participating entities
    emotional_valence: str = "neutral"  # positive/negative/neutral/mixed
    importance: float = 0.5       # 0.0-1.0
    happened_at: Optional[str] = None  # date when event occurred, e.g. "2023-05-07"

    def __repr__(self):
        return f"Episode({self.summary[:50]}... [{self.emotional_valence}])"


@dataclass
class ExtractedProcedure:
    """Extracted procedure — repeatable workflow/skill."""
    name: str                     # procedure name
    trigger: str = ""             # when to apply
    steps: list[dict] = field(default_factory=list)  # [{step, action, detail}]
    entities: list[str] = field(default_factory=list)  # related entities

    def __repr__(self):
        return f"Procedure({self.name}, steps={len(self.steps)})"


@dataclass
class ExtractionResult:
    """Result of knowledge extraction from conversation"""
    entities: list[ExtractedEntity] = field(default_factory=list)
    relations: list[ExtractedRelation] = field(default_factory=list)
    knowledge: list[ExtractedKnowledge] = field(default_factory=list)
    episodes: list[ExtractedEpisode] = field(default_factory=list)
    procedures: list[ExtractedProcedure] = field(default_factory=list)
    raw_response: str = ""

    def __repr__(self):
        return (
            f"ExtractionResult(entities={len(self.entities)}, "
            f"relations={len(self.relations)}, "
            f"knowledge={len(self.knowledge)}, "
            f"episodes={len(self.episodes)}, "
            f"procedures={len(self.procedures)})"
        )


class ConversationExtractor:
    """Extracts structured knowledge from conversations"""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def extract(self, conversation: list[dict], existing_context: str = "",
                prompt_version: str = None) -> ExtractionResult:
        conv_text = self._format_conversation(conversation)

        # Build context block
        if existing_context:
            context_block = EXISTING_CONTEXT_BLOCK.format(context=existing_context)
        else:
            context_block = ""

        version = prompt_version or EXTRACTION_PROMPT_VERSION
        prompt_template = EXTRACTION_PROMPT_V2 if version == "v2" else EXTRACTION_PROMPT
        prompt = prompt_template.format(
            conversation=conv_text,
            existing_context=context_block
        )
        try:
            raw_response = self.llm.complete(prompt, response_format=EXTRACTION_SCHEMA)
        except Exception as e:
            _logger.warning(f"Structured output failed ({type(e).__name__}: {e}), falling back to plain completion")
            raw_response = self.llm.complete(prompt)
        return self._parse_response(raw_response)

    def extract_from_text(self, text: str) -> ExtractionResult:
        conversation = [{"role": "user", "content": text}]
        return self.extract(conversation)

    def _format_conversation(self, conversation: list[dict]) -> str:
        lines = []
        for msg in conversation:
            role = "User" if msg["role"] == "user" else "Assistant"
            lines.append(f"{role}: {msg['content']}")
        return "\n\n".join(lines)

    def _parse_response(self, raw: str) -> ExtractionResult:
        result = ExtractionResult(raw_response=raw)

        # Parse JSON with fallback strategies
        data = None
        clean = raw.strip()

        # Strategy 1: Direct parse
        try:
            data = json.loads(clean)
        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 2: Strip markdown fences (handles text before ```)
        if data is None and "```" in clean:
            start = clean.find("```")
            end = clean.rfind("```")
            if start != end:
                inner = clean[start:end]
                lines = inner.split("\n", 1)
                if len(lines) > 1:
                    try:
                        data = json.loads(lines[1])
                    except (json.JSONDecodeError, ValueError):
                        pass

        # Strategy 3: Find outermost { }
        if data is None:
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(raw[start:end + 1])
                except (json.JSONDecodeError, ValueError):
                    pass

        if data is None:
            _logger.warning(f"⚠️ Failed to parse JSON from LLM (length: {len(raw)})")
            return result

        # Entities
        for e in data.get("entities", []):
            raw_facts = e.get("facts", [])
            parsed_facts = []
            for f in raw_facts:
                if isinstance(f, str):
                    parsed_facts.append(ExtractedFact(content=f))
                elif isinstance(f, dict):
                    parsed_facts.append(ExtractedFact(
                        content=_ensure_str(f.get("fact", f.get("content", ""))),
                        event_date=f.get("when", f.get("event_date")),
                    ))
            result.entities.append(ExtractedEntity(
                name=_ensure_str(e.get("name", "Unknown"), "Unknown"),
                entity_type=_ensure_str(e.get("type", "concept"), "concept"),
                facts=parsed_facts,
            ))

        # Relations
        for r in data.get("relations", []):
            result.relations.append(ExtractedRelation(
                from_entity=_ensure_str(r.get("from", "")),
                to_entity=_ensure_str(r.get("to", "")),
                relation_type=_ensure_str(r.get("type", "related_to"), "related_to"),
                description=_ensure_str(r.get("description", "")),
            ))

        # Knowledge (NEW)
        for k in data.get("knowledge", []):
            artifact = k.get("artifact")
            if artifact is not None and not isinstance(artifact, str):
                artifact = str(artifact)
            result.knowledge.append(ExtractedKnowledge(
                entity=_ensure_str(k.get("entity", "")),
                knowledge_type=_ensure_str(k.get("type", "insight"), "insight"),
                title=_ensure_str(k.get("title", "")),
                content=_ensure_str(k.get("content", "")),
                artifact=artifact,
            ))

        # Episodes (v2.5)
        for ep in data.get("episodes", []):
            happened = ep.get("happened_at")
            if happened and not isinstance(happened, str):
                happened = str(happened)
            if happened and happened.lower() in ("null", "none", "unknown", ""):
                happened = None
            try:
                importance = float(ep.get("importance", 0.5))
            except (ValueError, TypeError):
                importance = 0.5
            result.episodes.append(ExtractedEpisode(
                summary=_ensure_str(ep.get("summary", "")),
                context=_ensure_str(ep.get("context", "")),
                outcome=_ensure_str(ep.get("outcome", "")),
                participants=ep.get("participants", []),
                emotional_valence=_ensure_str(ep.get("emotional_valence", "neutral"), "neutral"),
                importance=importance,
                happened_at=happened,
            ))

        # Procedures (v2.5)
        for pr in data.get("procedures", []):
            steps = pr.get("steps", [])
            if isinstance(steps, list):
                clean_steps = []
                for s in steps:
                    if isinstance(s, dict):
                        clean_steps.append(s)
                    elif isinstance(s, str):
                        clean_steps.append({"step": len(clean_steps) + 1, "action": s, "detail": ""})
                    else:
                        clean_steps.append({"step": len(clean_steps) + 1, "action": str(s), "detail": ""})
                steps = clean_steps
            result.procedures.append(ExtractedProcedure(
                name=_ensure_str(pr.get("name", "")),
                trigger=_ensure_str(pr.get("trigger", "")),
                steps=steps,
                entities=pr.get("entities", []),
            ))

        return result


# --- Mock for testing ---

class MockLLMClient(LLMClient):
    """Mock LLM for testing without API"""

    def complete(self, prompt: str, system: str = "", response_format=None) -> str:
        return json.dumps({
            "entities": [
                {
                    "name": "User",
                    "type": "person",
                    "facts": [
                        "Works as backend developer",
                        {"fact": "Works at Uzum Bank", "when": "2024-01-15"},
                        "Main stack: Java, Spring Boot"
                    ]
                },
                {
                    "name": "Uzum Bank",
                    "type": "company",
                    "facts": ["Bank in Uzbekistan", "Microservices architecture"]
                },
                {
                    "name": "Project Alpha",
                    "type": "project",
                    "facts": ["Backend service for payments", "Problem with connection pool"]
                },
                {
                    "name": "PostgreSQL",
                    "type": "technology",
                    "facts": ["Main database", "Version 15"]
                },
                {
                    "name": "Spring Boot",
                    "type": "technology",
                    "facts": ["Main framework for microservices"]
                }
            ],
            "relations": [
                {"from": "User", "to": "Uzum Bank", "type": "works_at", "description": "Backend developer"},
                {"from": "User", "to": "Project Alpha", "type": "member_of", "description": "Works on project"},
                {"from": "Project Alpha", "to": "PostgreSQL", "type": "uses", "description": "Main database"},
                {"from": "Project Alpha", "to": "Spring Boot", "type": "uses", "description": "Backend framework"},
                {"from": "Uzum Bank", "to": "Project Alpha", "type": "related_to", "description": "Bank project"}
            ],
            "knowledge": [
                {
                    "entity": "PostgreSQL",
                    "type": "solution",
                    "title": "Connection pool exhaustion fix",
                    "content": "OOM with 200+ WebSocket connections. Each WS held a separate connection. Solution: Redis cache for UserService and BlockedAccountService.",
                    "artifact": "spring.datasource.hikari.maximum-pool-size: 20\nspring.datasource.hikari.idle-timeout: 30000\nspring.datasource.hikari.connection-timeout: 5000"
                },
                {
                    "entity": "PostgreSQL",
                    "type": "command",
                    "title": "Check active connections",
                    "content": "Monitoring active PostgreSQL connections",
                    "artifact": "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"
                }
            ],
            "episodes": [
                {
                    "summary": "Debugged PostgreSQL connection pool exhaustion",
                    "context": "200+ WebSocket connections caused OOM. Each WS held a separate DB connection. Investigated HikariCP settings.",
                    "outcome": "Fixed by adding Redis cache for UserService and BlockedAccountService, reduced pool size to 20",
                    "participants": ["PostgreSQL", "Project Alpha"],
                    "emotional_valence": "positive",
                    "importance": 0.7,
                    "happened_at": "2024-01-15"
                }
            ],
            "procedures": [
                {
                    "name": "Debug PostgreSQL connection issues",
                    "trigger": "When database connections are exhausted or OOM occurs",
                    "steps": [
                        {"step": 1, "action": "Check active connections", "detail": "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"},
                        {"step": 2, "action": "Review HikariCP pool settings", "detail": "Check maximum-pool-size, idle-timeout, connection-timeout"},
                        {"step": 3, "action": "Add caching layer", "detail": "Use Redis to cache frequently accessed services"}
                    ],
                    "entities": ["PostgreSQL", "Project Alpha"]
                }
            ]
        }, ensure_ascii=False)
