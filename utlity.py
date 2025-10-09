import os
from dotenv import load_dotenv , find_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain.chat_models import init_chat_model

load_dotenv(find_dotenv())

model = init_chat_model("gpt-4o-mini" , model_provider='openai')

class AI_Integration:
    @staticmethod
    def makeQuestion(jobDescription : str):
        #system template:
        system_template = (
            "You are a professional recruiter. You will be provided with a job description, "
            "and your task is to generate four short and clear **technical screening questions only**. "
            "Each question should be one simple sentence focused strictly on technical skills, "
            "tools, or technologies mentioned in the job description. "
            "Avoid questions about education, degrees, or general background."
        )

        prompt_template = ChatPromptTemplate.from_messages(
            [("system", system_template), ("user", "{jobDescription}")]
        )

        prompt = prompt_template.invoke({"jobDescription" : jobDescription})

        response = model.invoke(prompt)
        return response.content
        # print(response.content)

