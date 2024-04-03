import gradio as gr
from SendMessageOnce import SendMessageOnce
import AutoSendMessage


with gr.Blocks(theme=gr.themes.Soft()) as demo:
    with gr.Tab("发送一次信息"):
        gr.Interface(

            fn=SendMessageOnce,

            inputs=[
                gr.Textbox(label="Excel路径"),
                gr.Textbox(label="表格页"),
                gr.Textbox(label="好友名列"),
                gr.Textbox(label="发送信息列"),
                gr.Textbox(label="发送日期列"),
                gr.Textbox(label="添加日期列"),
                gr.Textbox(label="微信路径"),
                gr.Dropdown(choices=["zh-CN", "zh-TW", "en-US"], label="微信语言", value="zh-CN")

            ],
            outputs="text",
            description="每次按下发送按钮，会从表格中筛选出日期符合的用户，立即发送信息给他们",
            allow_flagging="never",

            examples=[["C:\\Users\\Public\\Downloads\\Wechat.xlsx", "Sheet1", "A", "B", "C", "D",
                       "C:\\Program Files\\Tencent\\WeChat\\WeChat.exe", "zh-CN"]],
            submit_btn="发送",
            clear_btn="清空",


        )
    with gr.Tab("自动发送信息"):
        gr.Interface(fn=AutoSendMessage.schedule_script_run,
                     inputs=[
                         gr.Textbox(label="Excel路径"),
                         gr.Textbox(label="表格页"),
                         gr.Textbox(label="好友名列"),
                         gr.Textbox(label="发送信息列"),
                         gr.Textbox(label="发送日期列"),
                         gr.Textbox(label="添加日期列"),
                         gr.Textbox(label="微信路径"),
                         gr.Dropdown(choices=["zh-CN", "zh-TW", "en-US"], label="微信语言", value="zh-CN"),
                         gr.Textbox(label="发送时间")
                     ],
                     outputs="text",
                     allow_flagging="never",
                     description="每天在指定时间，会从表格中筛选出日期符合的用户，发送信息给他们",
                     examples=[["C:\\Users\\Public\\Downloads\\Wechat.xlsx", "Sheet1", "A", "B", "C", "D",
                                "C:\\Program Files\\Tencent\\WeChat\\WeChat.exe", "zh-CN", "09:00"]],
                     submit_btn="开始自动发送",
                     clear_btn="清空",
        )


demo.launch(inbrowser=True,show_api=False)