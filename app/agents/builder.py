from .config import ToolConfig, load_agents_config
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools import google_search
from google.genai import types
from tools.corporate_documents_search import doc_rag_search
# Setup path to import corporate_documents_search from app/tools
#current_dir = os.path.dirname(os.path.abspath(__file__))
#project_dir = os.path.dirname(os.path.dirname(current_dir))
#tools_dir = os.path.join(project_dir, "app", "tools")
#if tools_dir not in sys.path:
#    sys.path.insert(0, tools_dir)

#from corporate_documents_search import doc_rag_search



config = load_agents_config(
    "agents.yaml"
)


research_agent = config.agents["research"]

print(research_agent.name)
print(research_agent.model)
print(research_agent.instructions)

for tool in research_agent.tools:
    print(tool.type)
    print(tool.params)

def create_tool_list(tool_configs: list[ToolConfig]) -> []:
    out = []
    for tc in tool_configs:
        type = tc.type
        params = tc.params

        if type == "google_search":
            out.append(google_search)
        elif type == "doc_rag_search" or type == "sec_rag_search":
            out.append(doc_rag_search)

    return out

class DualAwaitableGenerator:
    def __init__(self, awaitable_target=None, generator_target=None):
        self.awaitable_target = awaitable_target
        self.generator_target = generator_target

    def __await__(self):
        if self.awaitable_target is None:
            raise RuntimeError("This object is not awaitable in this context.")
        return self.awaitable_target.__await__()

    def __aiter__(self):
        if self.generator_target is None:
            raise RuntimeError("This object is not an async generator in this context.")
        return self.generator_target.__aiter__()

    async def aclose(self):
        if self.generator_target and hasattr(self.generator_target, 'aclose'):
            await self.generator_target.aclose()

class RunnableLlmAgent(Agent):
    def run_async(self, prompt_or_context, *args, **kwargs):
        if isinstance(prompt_or_context, str):
            async def run_prompt():
                from google.adk.runners import Runner
                from google.adk.sessions import InMemorySessionService
                from google.genai import types
                import asyncio
                import re

                max_attempts = 10
                for attempt in range(max_attempts):
                    try:
                        session_service = InMemorySessionService()
                        await session_service.create_session(app_name="app", user_id="default_user", session_id=f"session_{attempt}")
                        runner = Runner(agent=self, app_name="app", session_service=session_service)

                        text_content = ""
                        async for event in runner.run_async(
                            user_id="default_user",
                            session_id=f"session_{attempt}",
                            new_message=types.Content(role="user", parts=[types.Part.from_text(text=prompt_or_context)]),
                        ):
                            try:
                                debug_str = f"[DEBUG] author={event.author} is_final={event.is_final_response()} content={repr(event.content)}"
                                print(debug_str.encode('ascii', 'ignore').decode('ascii'))
                            except Exception:
                                pass
                            if event.content and event.content.parts:
                                for part in event.content.parts:
                                    if part.text:
                                        text_content += part.text

                        class AgentResponse:
                            def __init__(self, text):
                                self.text = text

                        return AgentResponse(text_content)
                    except Exception as e:
                        err_str = str(e)
                        if any(x in err_str for x in ["429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE"]):
                            if attempt < max_attempts - 1:
                                wait_time = 15
                                match = re.search(r"retry in ([\d\.]+)s", err_str, re.IGNORECASE)
                                if not match:
                                    match = re.search(r"retryDelay': '(\d+)s'", err_str, re.IGNORECASE)
                                if match:
                                    wait_time = int(float(match.group(1))) + 2
                                print(f"\n[Gemini API Quota/Service Hit ({'Rate Limit' if '429' in err_str else 'Service Overload'})] Retrying in {wait_time}s... (Attempt {attempt+1}/{max_attempts})")
                                await asyncio.sleep(wait_time)
                                continue
                        raise e

            return DualAwaitableGenerator(awaitable_target=run_prompt())
        else:
            generator = super().run_async(prompt_or_context, *args, **kwargs)
            return DualAwaitableGenerator(generator_target=generator)

def create_agent(agent_name):
    agent = config.agents[agent_name]
    normalized_name = agent.name.lower().replace(" ", "_")
    
    import datetime
    now = datetime.datetime.now()
    current_year = now.year
    current_date_str = now.strftime("%Y-%m-%d")
    current_time_str = now.strftime("%H:%M:%S")
    
    # Render instructions dynamically with current system year, date, and time
    formatted_instructions = agent.instructions
    try:
        formatted_instructions = agent.instructions.format(
            current_year=current_year,
            current_year_minus_1=current_year - 1,
            current_year_minus_2=current_year - 2,
            current_date=current_date_str,
            current_time=current_time_str
        )
    except KeyError:
        # Fallback if some agents instructions don't contain formatting placeholders
        pass
        
    # Prepend date-time metadata context header to the instructions
    metadata_header = (
        f"--- SYSTEM METADATA ---\n"
        f"Current Date: {current_date_str}\n"
        f"Current Time: {current_time_str}\n"
        f"Current Year: {current_year}\n"
        f"-----------------------\n\n"
    )
    formatted_instructions = metadata_header + formatted_instructions
        
    return RunnableLlmAgent(name=normalized_name, model=agent.model, instruction=formatted_instructions, tools=create_tool_list(agent.tools))

def create_financial_agent():
    return create_agent('financial')

def create_research_agent():
    return create_agent('research')

def create_risk_agent():
    return create_agent('risk')

def create_summary_agent():
    return create_agent('summary')

def create_valuation_agent():
    return create_agent('valuation')

def create_committee_agent():
    return create_agent('committee')