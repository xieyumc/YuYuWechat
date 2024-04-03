import schedule
import time
import subprocess

def run_script(     ExcelFile,
                    Sheet,
                    ExcelWechatName,
                    ExcelWechatSentMessage,
                    ExcelSentDate,
                    ExcelAddDate,
                    Path,
                    Locale):
    subprocess.run(["python", "SendMessageOnce.py",
                    ExcelFile,
                    Sheet,
                    ExcelWechatName,
                    ExcelWechatSentMessage,
                    ExcelSentDate,
                    ExcelAddDate,
                    Path,
                    Locale])
def schedule_script_run(ExcelFile,
                    Sheet,
                    ExcelWechatName,
                    ExcelWechatSentMessage,
                    ExcelSentDate,
                    ExcelAddDate,
                    Path,
                    Locale,
                    RunTime):
    schedule.every().day.at(RunTime).do(run_script(ExcelFile,
                    Sheet,
                    ExcelWechatName,
                    ExcelWechatSentMessage,
                    ExcelSentDate,
                    ExcelAddDate,
                    Path,
                    Locale))
    while True:
        schedule.run_pending()
        time.sleep(50)

if __name__ == "__main__":
    schedule_script_run()