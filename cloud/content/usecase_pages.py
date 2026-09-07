"""Industry use-case pages: /usecase/{slug}.

Moved verbatim out of cloud/api.py; rendered by cloud/site.py."""

USECASE_PAGES = {
    "customer-support": {
        "slug": "customer-support",
        "industry": "customer support",
        "icon": "🎧",
        "title": "AI Memory for Customer Support Agents",
        "hero_description": "Support agents that remember every customer interaction. No more asking customers to repeat themselves.",
        "seo_title": "AI Memory for Customer Support Agents | Mengram",
        "seo_description": "Give your customer support AI agents persistent memory. Remember customer history, preferences, and past issues across every interaction. Reduce resolution time by 40%.",
        "seo_keywords": "AI memory customer support, AI customer service memory, support agent memory, customer context AI, persistent memory support bot",
        "pain_points": [
            ("Customers repeat themselves", "Every new session starts from zero. Customers explain their issue again and again across channels and agents."),
            ("No context between sessions", "When a customer returns, the AI has no idea about previous interactions, resolutions, or preferences."),
            ("Generic responses", "Without history, the AI gives cookie-cutter answers instead of personalized solutions based on the customer's product usage."),
            ("Slow resolution times", "Agents spend time gathering context instead of solving problems. Each ticket starts from scratch."),
        ],
        "solutions": [
            ("Full customer history", "Semantic memory stores customer preferences, plan details, and product usage. Episodic memory recalls past issues and resolutions."),
            ("Cross-session continuity", "Every interaction enriches the customer's memory. Next time they reach out, the AI already knows their history."),
            ("Personalized resolution", "Cognitive Profile generates a system prompt with everything known about the customer — preferences, history, and escalation patterns."),
            ("Workflow learning", "Procedural memory captures resolution workflows that improve from failures. The AI learns the best process for each issue type."),
        ],
        "code_example": """from mengram import Mengram
from openai import OpenAI

m = Mengram(api_key="mg-...")
openai = OpenAI()

def handle_ticket(customer_id: str, message: str):
    # Get full customer context in one call
    profile = m.profile(user_id=customer_id)
    past_issues = m.search(message, user_id=customer_id, top_k=3)

    context = "\\n".join([r.memory for r in past_issues])

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": profile},
            {"role": "user", "content": f"Past issues:\\n{context}\\n\\nNew message: {message}"}
        ]
    )

    # Store this interaction for future context
    m.add(f"Customer: {message}\\nAgent: {response.choices[0].message.content}",
          user_id=customer_id)
    return response.choices[0].message.content""",
        "benefits": [
            ("40%", "Faster resolution"),
            ("3x", "Customer satisfaction"),
            ("Zero", "Context switching"),
        ],
    },
    "personal-assistant": {
        "slug": "personal-assistant",
        "industry": "personal assistant",
        "icon": "🤖",
        "title": "AI Memory for Personal Assistants",
        "hero_description": "Build AI assistants that truly know their users. Remember preferences, habits, and context across every conversation.",
        "seo_title": "AI Memory for Personal Assistants | Mengram",
        "seo_description": "Build AI personal assistants with persistent memory. Remember user preferences, habits, schedules, and context. Cognitive Profile for instant personalization.",
        "seo_keywords": "AI personal assistant memory, persistent memory assistant, AI companion memory, personalized AI assistant, Mengram personal assistant",
        "pain_points": [
            ("Every day is day one", "Personal assistants forget everything between sessions. Users re-explain preferences, projects, and context every time."),
            ("No personalization", "Without memory, the assistant gives generic responses that don't reflect the user's unique needs and style."),
            ("Can't learn habits", "The assistant can't recognize patterns in the user's behavior — daily routines, recurring tasks, or preferred workflows."),
            ("No relationship building", "AI companions feel shallow because they don't accumulate shared experiences or inside references."),
        ],
        "solutions": [
            ("Deep personalization", "Semantic memory stores preferences, interests, and personal details. The AI knows the user inside and out."),
            ("Shared history", "Episodic memory remembers conversations, decisions, and events. The AI references past interactions naturally."),
            ("Learned routines", "Procedural memory captures daily workflows, recurring tasks, and preferred processes that evolve over time."),
            ("Cognitive Profile", "One API call generates a system prompt with the user's full context — making every LLM instantly personalized."),
        ],
        "code_example": """from mengram import Mengram

m = Mengram(api_key="mg-...")

# Morning check-in — AI remembers everything
profile = m.profile(user_id="alice")
# "Alice is a product manager at Acme Corp. She prefers morning standup
#  summaries with bullet points. She's working on the Q1 launch...
#  Yesterday she reviewed the design specs and had feedback on the nav..."

# After each conversation, memory grows
m.add("Alice asked me to remind her about the design review on Friday. "
      "She also mentioned she prefers Figma links over screenshots.",
      user_id="alice")

# Next session: the AI remembers the reminder and preference
memories = m.search("design review", user_id="alice")""",
        "benefits": [
            ("100%", "Context retention"),
            ("∞", "Session continuity"),
            ("3 types", "Memory depth"),
        ],
    },
    "education": {
        "slug": "education",
        "industry": "education",
        "icon": "📚",
        "title": "AI Memory for Education & Adaptive Tutoring",
        "hero_description": "AI tutors that remember what each student knows, where they struggle, and how they learn best.",
        "seo_title": "AI Memory for Education & Adaptive Tutoring | Mengram",
        "seo_description": "Build AI tutors with persistent memory. Track student knowledge, learning style, and progress. Adaptive tutoring that gets smarter with every session.",
        "seo_keywords": "AI memory education, AI tutoring memory, adaptive learning AI, personalized education AI, AI tutor memory, Mengram education",
        "pain_points": [
            ("No student model", "AI tutors don't track what the student knows vs. doesn't know. They can't adapt difficulty or skip mastered topics."),
            ("Repeated explanations", "Students get the same explanation style even when it didn't work before. No adaptation to individual learning patterns."),
            ("Lost progress", "Each tutoring session starts fresh. Past mistakes, breakthroughs, and learning trajectory are forgotten."),
            ("One-size-fits-all", "Without memory, every student gets the same experience regardless of their level, goals, or learning speed."),
        ],
        "solutions": [
            ("Knowledge tracking", "Semantic memory stores what each student knows, their knowledge gaps, and mastery levels per topic."),
            ("Learning history", "Episodic memory records tutoring sessions — which explanations worked, what confused the student, key breakthroughs."),
            ("Teaching strategies", "Procedural memory captures effective tutoring approaches per student that improve over time."),
            ("Adaptive profiles", "Cognitive Profile generates a tutor system prompt with the student's full context — level, preferences, and history."),
        ],
        "code_example": """from mengram import Mengram

m = Mengram(api_key="mg-...")

def tutor_session(student_id: str, topic: str):
    # Get student's full learning profile
    profile = m.profile(user_id=student_id)
    # "Student is a 10th grader studying calculus. Strong in algebra,
    #  struggles with limits. Learns best with visual examples.
    #  Last session: practiced chain rule, got 7/10 correct."

    past = m.search(topic, user_id=student_id)
    # Returns past interactions with this topic

    # After the session, store progress
    m.add(f"Tutored {topic}. Student understood the concept after "
          f"visual explanation with graphs. Scored 8/10 on practice.",
          user_id=student_id)""",
        "benefits": [
            ("2x", "Learning speed"),
            ("85%", "Retention rate"),
            ("Per-student", "Adaptation"),
        ],
    },
    "healthcare": {
        "slug": "healthcare",
        "industry": "healthcare",
        "icon": "🏥",
        "title": "AI Memory for Healthcare Agents",
        "hero_description": "Healthcare AI that remembers patient context, medical history, and care preferences across every interaction.",
        "seo_title": "AI Memory for Healthcare Agents | Mengram",
        "seo_description": "Build healthcare AI agents with persistent memory. Track patient context, medical preferences, and care history. Self-hostable for data sovereignty.",
        "seo_keywords": "AI memory healthcare, healthcare AI memory, patient context AI, medical AI memory, healthcare agent memory, Mengram healthcare",
        "pain_points": [
            ("Repeated intake questions", "Patients describe their history, medications, and symptoms every time they interact with the AI assistant."),
            ("No care continuity", "AI health assistants don't track conversations over time — missing patterns in symptoms, mood, or behavior."),
            ("Generic health advice", "Without patient context, AI gives generic recommendations instead of personalized guidance based on history."),
            ("Data sovereignty concerns", "Healthcare data must stay within controlled environments. Cloud-only solutions don't meet compliance needs."),
        ],
        "solutions": [
            ("Patient context", "Semantic memory stores patient preferences, conditions, and care notes. Always available for personalized interactions."),
            ("Interaction history", "Episodic memory tracks symptom reports, mood changes, and care interactions over time — surfacing patterns."),
            ("Care workflows", "Procedural memory captures proven care pathways and follow-up procedures that improve with each patient interaction."),
            ("Self-hostable", "Deploy Mengram on your own infrastructure. All memory stays within your data boundary. MIT licensed."),
        ],
        "code_example": """from mengram import Mengram

# Self-hosted for data sovereignty
m = Mengram(base_url="https://your-mengram.internal.com")

def patient_interaction(patient_id: str, message: str):
    # Full patient context in one call
    profile = m.profile(user_id=patient_id)
    # "Patient is managing Type 2 diabetes. Prefers morning check-ins.
    #  Last reported A1C: 7.2%. Current medications: metformin.
    #  Last visit: discussed increasing exercise routine."

    # Search for relevant history
    history = m.search(message, user_id=patient_id)

    # After interaction, store for continuity
    m.add(f"Patient reported: {message}", user_id=patient_id)""",
        "benefits": [
            ("100%", "Context retention"),
            ("Self-host", "Data sovereignty"),
            ("HIPAA", "Ready architecture"),
        ],
    },
    "sales": {
        "slug": "sales",
        "industry": "sales",
        "icon": "💼",
        "title": "AI Memory for Sales & SDR Agents",
        "hero_description": "Sales AI that remembers every prospect interaction, objection, and follow-up across the entire pipeline.",
        "seo_title": "AI Memory for Sales & SDR Agents | Mengram",
        "seo_description": "Build sales AI agents with persistent memory. Track prospect interactions, objections, pain points, and follow-ups. AI SDR that gets smarter with every call.",
        "seo_keywords": "AI memory sales, AI SDR memory, sales agent memory, prospect context AI, AI sales assistant, Mengram sales",
        "pain_points": [
            ("Cold outreach feels cold", "AI SDRs send generic messages because they don't remember past interactions or prospect context."),
            ("Lost follow-up context", "Between calls, the AI forgets what was discussed — objections raised, interests expressed, next steps agreed."),
            ("No objection learning", "Every objection is handled from scratch. The AI doesn't learn which responses work best for each prospect type."),
            ("Pipeline blind spots", "Without memory, AI can't track where each prospect is in the journey or what triggered their interest."),
        ],
        "solutions": [
            ("Prospect intelligence", "Semantic memory stores company info, role, pain points, and interests discovered across interactions."),
            ("Full interaction history", "Episodic memory records every call, email, and meeting — what was discussed, what resonated, what fell flat."),
            ("Objection playbooks", "Procedural memory captures winning responses to common objections that improve from successful closes."),
            ("Pipeline context", "Cognitive Profile generates a briefing for each prospect — full history, next steps, and recommended approach."),
        ],
        "code_example": """from mengram import Mengram

m = Mengram(api_key="mg-...")

def prep_for_call(prospect_id: str):
    # Get full prospect briefing
    profile = m.profile(user_id=prospect_id)
    # "Prospect is VP Engineering at TechCo (Series B, 50 engineers).
    #  Pain point: context switching between tools.
    #  Last call: interested in the API, asked about pricing.
    #  Objection: concerned about vendor lock-in.
    #  Next step: send case study from similar company."

    return profile

def after_call(prospect_id: str, notes: str):
    # Store call outcome for next interaction
    m.add(notes, user_id=prospect_id)
    # "Called prospect. Addressed vendor lock-in concern with MIT license
    #  and self-hosting option. They want a demo next Tuesday."
""",
        "benefits": [
            ("3x", "Response rate"),
            ("60%", "Faster pipeline"),
            ("Zero", "Context loss"),
        ],
    },
}
