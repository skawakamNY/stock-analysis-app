from openai import OpenAI

print("before")

client = OpenAI(
    api_key="test"
)

print("after")