import io
import sys

import ProcessedExcel
import AutoWechat
import time

def SendMessageOnce(ExcelFile,
                    Sheet,
                    ExcelWechatName,
                    ExcelWechatSentMessage,
                    ExcelSentDate,
                    ExcelAddDate,
                    Path,
                    Locale):

    buffer=io.StringIO()
    sys.stdout=buffer


    try:
        Excel=ProcessedExcel.Excel(ExcelFile,Sheet,ExcelWechatName,ExcelWechatSentMessage,ExcelSentDate,ExcelAddDate)
    except Exception as e:
        print("读取表格失败，请检查路径和表格是否符合格式要求\n\n",e)
        sys.stdout = sys.__stdout__
        output = buffer.getvalue()
        buffer.close()

        return output


    try:
        Wechat=AutoWechat.WeChat(Path,Locale)
    except Exception as e:
        print("请检查微信路径和语言是否正确\n\n",e)
        sys.stdout = sys.__stdout__
        output = buffer.getvalue()
        buffer.close()

        return output


    try:
        Excel.FilterExcel()
    except Exception as e:
        print("请检查表格是否符合格式要求\n\n",e)
        sys.stdout = sys.__stdout__
        output = buffer.getvalue()
        buffer.close()

        return output


    try:
        Excel.GetInformation()
    except Exception as e:
        print("请检查表格是否符合格式要求\n\n",e)
        sys.stdout = sys.__stdout__
        output = buffer.getvalue()
        buffer.close()

        return output


    print(f"发送给{len(Excel.ProcessedList)}个好友\n")
    try:
        for i in range(len(Excel.ProcessedList)):

            print("发送给:",Excel.Information[i][0],"信息:",Excel.Information[i][1],"\n")
            Wechat.send_msg(Excel.Information[i][0],Excel.Information[i][1])
            try:
                Excel.AddDate(Excel.ProcessedList[i])
            except Exception as ea:
                print("添加日期失败\n\n",ea)


        print("发送结束,已给表格添加日期\n")

    except Exception as e:
        print("发送信息失败\n\n",e)
        sys.stdout = sys.__stdout__
        output = buffer.getvalue()
        buffer.close()

        return output



    sys.stdout=sys.__stdout__
    output=buffer.getvalue()
    buffer.close()

    return output
