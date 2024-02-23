from botbuilder.dialogs import DialogTurnStatus, Dialog,PromptOptions , DialogSet, WaterfallDialog, WaterfallStepContext, WaterfallStepContext, DialogTurnResult,ComponentDialog
from botbuilder.dialogs.prompts import (
    TextPrompt,
    PromptOptions,
)
from azure.ai.textanalytics import TextAnalyticsClient
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv
import os
import traceback
import sys
from datetime import datetime
from aiohttp import web
from aiohttp.web import Request, Response, json_response
from botbuilder.core import (
    BotFrameworkAdapter, UserState,
    BotFrameworkAdapterSettings,
    TurnContext,
    ActivityHandler,
    StatePropertyAccessor,
    ConversationState,
    MemoryStorage,
    MessageFactory,
    CardFactory
)
import asyncio
from config import DefaultConfig
import requests, uuid, json
from azure.ai.language.conversations import ConversationAnalysisClient
from botbuilder.schema import Activity, ActivityTypes,ChannelAccount,HeroCard,CardAction,CardImage,ActionTypes
from botbuilder.dialogs.prompts import PromptOptions, TextPrompt
from botbuilder.core.integration import aiohttp_error_middleware


CONFIG = DefaultConfig()

# Initialize Bot Framework Adapter
SETTINGS = BotFrameworkAdapterSettings(CONFIG.APP_ID, CONFIG.APP_PASSWORD)
ADAPTER = BotFrameworkAdapter(SETTINGS)

# Catch-all for errors.
async def on_error(context: TurnContext, error: Exception):
    # This check writes out errors to console log .vs. app insights.
    # NOTE: In production environment, you should consider logging this to Azure
    #       application insights.
    print(f"\n [on_turn_error] unhandled error: {error}", file=sys.stderr)
    traceback.print_exc()

    # Send a message to the user
    await context.send_activity("The bot encountered an error or bug.")
    await context.send_activity(
        "To continue to run this bot, please fix the bot source code."
    )
    # Send a trace activity if we're talking to the Bot Framework Emulator
    if context.activity.channel_id == "emulator":
        # Create a trace activity that contains the error object
        trace_activity = Activity(
            label="TurnError",
            name="on_turn_error Trace",
            timestamp=datetime.utcnow(),
            type=ActivityTypes.trace,
            value=f"{error}",
            value_type="https://www.botframework.com/schemas/error",
        )
        # Send a trace activity, which will be displayed in Bot Framework Emulator
        await context.send_activity(trace_activity)

ADAPTER.on_turn_error = on_error

load_dotenv()
# Initialize Azure services
ls_prediction_endpoint = os.getenv('LS_CONVERSATIONS_ENDPOINT')
ls_prediction_key = os.getenv('LS_CONVERSATIONS_KEY')

cqa_prediction_url = os.getenv('cqa_prediction_url')
cqa_key = os.getenv('cqa_key')

clu_client = ConversationAnalysisClient(ls_prediction_endpoint, AzureKeyCredential(ls_prediction_key))
cls_project = 'Fitness'
deployment_slot = 'fitness_clu_production'

translation_key = os.getenv('COG_SERVICE_KEY')
translation_endpoint = 'https://api.cognitive.microsofttranslator.com'
cog_region = os.getenv('COG_SERVICE_REGION')

text_analytics_key = os.getenv('SEN_SERVICE_KEY')
text_analytics_endpoint = os.getenv('SEN_SERVICE_ENDPOINT')
text_analytics_client = TextAnalyticsClient(text_analytics_endpoint, AzureKeyCredential(text_analytics_key))

# Dialogs classes here
class DialogHelper:
    @staticmethod
    async def run_dialog(
        dialog: Dialog, turn_context: TurnContext, accessor: StatePropertyAccessor
    ):
        dialog_set = DialogSet(accessor)
        dialog_set.add(dialog)

        dialog_context = await dialog_set.create_context(turn_context)
        results = await dialog_context.continue_dialog()
        if results.status == DialogTurnStatus.Empty:
            await dialog_context.begin_dialog(dialog.id)


class MainDialog(ComponentDialog):
    def __init__(self,user_state: UserState):
        super(MainDialog,self).__init__(CancelMembershipDialog.__name__)
        self.add_dialog(TextPrompt(TextPrompt.__name__))
        self.add_dialog(gymlocation('gymlocation'))
        self.add_dialog(CancelMembershipDialog('cancelMembershipDialog'))
        self.add_dialog(FeedbackDialog('feedbackDialog'))

        self.add_dialog(
            WaterfallDialog("WFDialog", [self.initial_step])
        )
        self.initial_dialog_id = "WFDialog"
    
    async def initial_step(self, step_context: WaterfallStepContext):
        user_input = step_context.context.activity.text.lower()

        lan_result = text_analytics_client.detect_language(documents=[user_input])[0].primary_language.iso6391_name
        global language_code 
        language_code = lan_result.split('_')[0]
        
        if language_code!='en':
            user_input2= await translate_to_english(user_input)
            print(user_input2)
        else:
            user_input2=user_input

        # Call CLU recognizer to get user intent and entities
        top_intent, confidence = await clu_intent(user_input2)
        message = f"[DEV INFO CLU_INTENT]  \ntop_intent: {top_intent}, confidence: {confidence}  \n [only for cancel membership & gym location intent and score of >0.8]"
        await step_context.context.send_activity(message)

        if confidence >= 0.8 and top_intent == "gymlocation" :
            return await step_context.begin_dialog('gymlocation')
        
        elif confidence >= 0.8 and top_intent == "CancelMembership" :
            return await step_context.begin_dialog('cancelMembershipDialog')
        
        elif user_input=='feedback' or user_input=='反馈':
            return await step_context.begin_dialog('feedbackDialog')
        else:
            qna_response = await get_qna_response(user_input2)
            print(qna_response)
            if language_code!='en':
                qna_response2= await translate_to_chinese(qna_response)
            else:
                qna_response2=qna_response
            await step_context.context.send_activity(qna_response2)
            return await step_context.end_dialog()

class gymlocation(ComponentDialog):
    def __init__(self, dialog_id: str = None):
        super(gymlocation, self).__init__(dialog_id or gymlocation.__name__)
        self.add_dialog(WaterfallDialog("new_membership", [self.process_intent]))
        self.initial_dialog_id = "new_membership"

    async def process_intent(self, step_context: WaterfallStepContext):
        card = HeroCard(
            title="welcome to Fitness First!",
            text="Time to work out! click button for the full address",
            images=[CardImage(url="https://aka.ms/bf-welcome-card-image")],
            buttons=[
                CardAction(
                    type=ActionTypes.open_url,
                    title="321 Clementi",
                    text="321 Clementi",
                    display_text="321-clementi",
                    value="https://www.fitnessfirst.com.sg/clubs/321-clementi",
                ),
                CardAction(
                    type=ActionTypes.open_url,
                    title="100AM Tanjong Pagar",
                    text="100AM Tanjong Pagar",
                    display_text="100AM Tanjong Pagar",
                    value="https://www.fitnessfirst.com.sg/clubs/100-am-tanjong-pagar",
                ),
                CardAction(
                    type=ActionTypes.open_url,
                    title="Paya Lebar",
                    text="Paya Lebar",
                    display_text="Paya Lebar",
                    value="https://www.fitnessfirst.com.sg/clubs/paya-lebar-singpost-centre",
                ),
            ],
        )
        await step_context.context.send_activity(MessageFactory.attachment(CardFactory.hero_card(card)))
        return await step_context.end_dialog()
    
class CancelMembershipDialog(ComponentDialog):
    def __init__(self, dialog_id: str = None):
        super(CancelMembershipDialog, self).__init__(dialog_id or CancelMembershipDialog.__name__)
        self.add_dialog(
            WaterfallDialog(
                WaterfallDialog.__name__,
                [
                    self.first_step,
                    self.second_step,
                ],))
        self.add_dialog(TextPrompt(TextPrompt.__name__))
        self.initial_dialog_id = WaterfallDialog.__name__

    async def first_step(self, step_context: WaterfallStepContext) -> DialogTurnResult:
        # Your implementation for the first step of the waterfall dialog
        if language_code!='en':
            return await step_context.prompt(TextPrompt.__name__, PromptOptions(prompt=MessageFactory.text("输入您的会员ID")))

        else:
            return await step_context.prompt(TextPrompt.__name__, PromptOptions(prompt=MessageFactory.text("Enter your membership ID.")))

    async def second_step(self, step_context: WaterfallStepContext) -> DialogTurnResult:
        if language_code!='en':
            await step_context.context.send_activity(MessageFactory.text(f"会员ID结尾为 {step_context.result} 已发起取消申请."))

        else:
            await step_context.context.send_activity(MessageFactory.text(f"Membership ID {step_context.result} cancellation initiated."))
        
        return await step_context.end_dialog()

class FeedbackDialog(ComponentDialog):
    def __init__(self, dialog_id: str = None):
        super(FeedbackDialog, self).__init__(dialog_id or FeedbackDialog.__name__)
        self.add_dialog(WaterfallDialog("feedback", [self.inputprompt,self.process_feedback],))

        self.add_dialog(TextPrompt(TextPrompt.__name__))

        self.initial_dialog_id = "feedback"

    async def inputprompt(self, step_context: WaterfallStepContext) -> DialogTurnResult:
        # Your implementation for the first step of the waterfall dialog
        if language_code!='en':
            return await step_context.prompt(TextPrompt.__name__, PromptOptions(prompt=MessageFactory.text("请输入您的反馈")))

        else:
            return await step_context.prompt(TextPrompt.__name__, PromptOptions(prompt=MessageFactory.text("Please input your feedback.")))
    
    async def process_feedback(self, step_context: WaterfallStepContext):
        feedback_text = step_context.context.activity.text
        sentiment,max_score = analyze_sentiment(feedback_text)
        if language_code!='en':
            sentiment2= await translate_to_chinese(sentiment)
            msg= f"感谢您的反馈意见！! 情绪: {sentiment2}, 分数: {max_score}"

        else:
            msg= f"Thank you for your feedback! Sentiment: {sentiment}, Score: {max_score}"
        
        await step_context.context.send_activity(msg)
        return await step_context.end_dialog()

#AI services function here
def analyze_sentiment(text):
    result = text_analytics_client.analyze_sentiment(documents=[text])
    confidence_scores = result[0].confidence_scores
    max_score = max(confidence_scores.__dict__.values())

    return result[0].sentiment,max_score

async def clu_intent(text):
    result = clu_client.analyze_conversation(
                        task={
                            "kind": "Conversation",
                            "analysisInput": {
                                "conversationItem": {
                                    "participantId": "1",
                                    "id": "1",
                                    "modality": "text",
                                    "language": "en",
                                    "text": text
                                },
                                "isLoggingEnabled": False
                            },
                            "parameters": {
                                "projectName": cls_project,
                                "deploymentName": deployment_slot,
                                "verbose": True
                            }
                        }
                    )
    top_intent = result["result"]["prediction"]["topIntent"]
    confidence = result["result"]["prediction"]["intents"][0]["confidenceScore"]
    return top_intent, confidence

async def get_qna_response(query):
    headers = {
    'Ocp-Apim-Subscription-Key': cqa_key,
    'Content-Type': 'application/json'}
    data = {
        'question': query
    }
    caq_response = requests.post(cqa_prediction_url, headers=headers, json=data)

    if caq_response.status_code == 200:
        result = caq_response.json()
        #D_response = await translate_to_chinese(result['answers'][0]['answer'])
        return result['answers'][0]['answer'] #D_response
    else:
        return{caq_response.status_code}

async def translate_to_chinese(text):
    params = {
        'api-version': '3.0',
        'from': 'en',
        'to': ['zh-Hans']
    }
    path = '/translate'
    constructed_url = translation_endpoint + path
    headers = {
        'Ocp-Apim-Subscription-Key': translation_key,
        # location required if you're using a multi-service or regional (not global) resource.
        'Ocp-Apim-Subscription-Region': cog_region,
        'Content-type': 'application/json',
        'X-ClientTraceId': str(uuid.uuid4())
    }
    # You can pass more than one object in body.
    body = [{'text': text}]
    # Send the request and get response
    request = requests.post(constructed_url, params=params, headers=headers, json=body)
    response = request.json()
    # Parse JSON array and get translation
    result = response[0]["translations"][0]["text"]
    return result

async def translate_to_english(text):
    params = {
        'api-version': '3.0',
        'from': 'zh',
        'to': ['en']
    }
    path = '/translate'
    constructed_url = translation_endpoint + path
    headers = {
        'Ocp-Apim-Subscription-Key': translation_key,
        # location required if you're using a multi-service or regional (not global) resource.
        'Ocp-Apim-Subscription-Region': cog_region,
        'Content-type': 'application/json',
        'X-ClientTraceId': str(uuid.uuid4())
    }
    # You can pass more than one object in body.
    body = [{'text': text}]
    # Send the request and get response
    request = requests.post(constructed_url, params=params, headers=headers, json=body)
    response = request.json()
    # Parse JSON array and get translation
    result = response[0]["translations"][0]["text"]
    return result

# Bot Class here
class FitnessBot(ActivityHandler):
    def __init__(self, conversation_state: ConversationState, user_state: UserState,dialog: Dialog):
        self.conversation_state = conversation_state
        self.user_state = user_state
        self.dialog = dialog

        if conversation_state is None:
            raise TypeError(
                "[DialogBot]: Missing parameter. conversation_state is required but None was given"
            )
        if user_state is None:
            raise TypeError(
                "[DialogBot]: Missing parameter. user_state is required but None was given"
            )
        # if dialog is None:
        #      raise Exception("[DialogBot]: Missing parameter. dialog is required")

    async def on_turn(self, turn_context: TurnContext):
        await super().on_turn(turn_context)
        # Save any state changes that might have occurred during the turn.
        await self.conversation_state.save_changes(turn_context, False)
        await self.user_state.save_changes(turn_context, False)

    async def on_members_added_activity(self, members_added: [ChannelAccount], turn_context: TurnContext):
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                # Send a one-time welcome message or instruction to new members
                await turn_context.send_activity("Welcome to the Fitness first!  \n\nfor English input English  \n\nYou may use chat for  \n[why i need to workout] for CQA  \n[Cancel Membership]  \n [Gym Locations]  \n[feedback]  \n Others chat through our FAQ-CQA \n\n欢迎来到健身第一  \n\n如需中文请输入中文  \n您可以使用聊天功能  \n\n为什么我需要锻炼  \n取消会员资格  \n健身房地点  \n反馈  \n聊天以了解更多信息")

    async def on_message_activity(self, turn_context: TurnContext):
        await DialogHelper.run_dialog(
            self.dialog,
            turn_context,
            self.conversation_state.create_property("DialogState"),
        )

# Create MemoryStorage, ConversationState and UserState
memory = MemoryStorage()
conversation_state = ConversationState(memory)
user_state = UserState(memory)

# Create the Bot and main main dialog
dialog = (MainDialog(user_state))
BOT = FitnessBot(conversation_state, user_state, dialog)

# Listen for incoming requests on /api/messages
async def messages(req: Request) -> Response:
    # Main bot message handler.
    if "application/json" in req.headers["Content-Type"]:
        body = await req.json()
    else:
        return Response(status=415)

    activity = Activity().deserialize(body)
    auth_header = req.headers["Authorization"] if "Authorization" in req.headers else ""

    try:
        response = await ADAPTER.process_activity(activity, auth_header, BOT.on_turn)
        if response:
            return json_response(data=response.body, status=response.status)
        return Response(status=201)
    except Exception as exception:
        raise exception

APP = web.Application(middlewares=[aiohttp_error_middleware])
APP.router.add_post("/api/messages", messages)

#run application
if __name__ == "__main__":
    try:
        web.run_app(APP, host="localhost", port=CONFIG.PORT)
    except Exception as error:
        raise error