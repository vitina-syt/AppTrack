"""
GPT对话模块
使用Azure OpenAI API与GPT模型对话
"""
import os
import logging
import requests
import base64
import time
from typing import Optional, List, Union
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GPTAssistant:
    """GPT助手类，负责与GPT模型对话"""
    
    def __init__(self, 
                 endpoint: Optional[str] = None,
                 api_key: Optional[str] = None, 
                 api_version: Optional[str] = None,
                 deployment_name: Optional[str] = None,
                 model: Optional[str] = None):
        """
        初始化GPT助手
        
        Args:
            endpoint: Azure OpenAI端点URL，如果为None则从环境变量读取
            api_key: Azure OpenAI API密钥，如果为None则从环境变量读取
            api_version: API版本，如果为None则从环境变量读取或使用默认值
            deployment_name: 部署名称，如果为None则从环境变量读取或使用默认值
            model: 保留此参数以保持兼容性，实际使用deployment_name
        """
        # 从环境变量读取配置，如果没有则使用testgpt.py中的默认值
        self.endpoint = endpoint or os.getenv("AZURE_OPENAI_ENDPOINT") or "https://oai-seaidev-concept-advisor.openai.azure.com/"
        self.api_key = api_key or os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.api_version = api_version or os.getenv("AZURE_OPENAI_API_VERSION") or "2025-01-01-preview"
        self.deployment_name = deployment_name or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME") or "ConceptAdvisor_GPT-5"
        
        if not self.api_key:
            raise ValueError("未提供Azure OpenAI API密钥，请设置AZURE_OPENAI_API_KEY或OPENAI_API_KEY环境变量")
        
        # 确保endpoint以/结尾
        if not self.endpoint.endswith("/"):
            self.endpoint += "/"
        
        # 构建API URL
        self.api_url = f"{self.endpoint}openai/deployments/{self.deployment_name}/chat/completions"
        
        logger.info(f"初始化Azure OpenAI客户端，端点: {self.endpoint}, 部署: {self.deployment_name}")
        
        # 系统提示词，指导GPT理解AutoCAD绘图需求
        self.system_prompt = """你是一个生活管家，可以回答任何日常生活的问题"""
    
    def chat(self, user_input: str) -> Optional[str]:
        """
        与GPT对话，获取AutoCAD命令
        
        Args:
            user_input: 用户的自然语言输入
            
        Returns:
            AutoCAD LISP命令字符串，如果出错则返回None
        """
        try:
            logger.info(f"向Azure GPT发送请求: {user_input}")
            
            # 准备请求参数
            params = {"api-version": self.api_version}
            
            headers = {
                "Content-Type": "application/json",
                "api-key": self.api_key
            }
            
            # GPT-5请求体 - 使用max_completion_tokens参数
            data = {
                "messages": [
                    {
                        "role": "system",
                        "content": self.system_prompt
                    },
                    {
                        "role": "user",
                        "content": user_input
                    }
                ],
                "max_completion_tokens": 8192  # GPT-5必须使用此参数
            }
            
            # 发送请求
            # 使用元组设置连接超时和读取超时：(连接超时, 读取超时)
            # 连接超时：建立连接的最大等待时间
            # 读取超时：等待服务器响应的最大时间
            response = requests.post(
                self.api_url, 
                headers=headers, 
                params=params, 
                json=data,
                timeout=(10, 120)  # (连接超时10秒, 读取超时120秒)
            )
            
            # 检查HTTP状态码
            response.raise_for_status()
            
            # 解析响应
            result_json = response.json()
            result = result_json["choices"][0]["message"]["content"].strip()
            
            logger.info(f"Azure GPT返回结果: {result}")
            
            # 清理可能存在的markdown标记
            result = self._clean_response(result)
            
            return result
            
        except requests.exceptions.Timeout as e:
            logger.error(f"Azure GPT API请求超时: {e}")
            logger.error("请求超时，可能是网络连接较慢或服务器响应时间过长，请稍后重试")
            return None
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Azure GPT API连接失败: {e}")
            logger.error("无法连接到服务器，请检查网络连接")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Azure GPT API请求失败: {e}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_detail = e.response.text
                    logger.error(f"错误详情: {error_detail}")
                except:
                    pass
            return None
        except Exception as e:
            logger.error(f"GPT对话失败: {e}")
            logger.exception("详细错误信息:")
            return None
    
    def _clean_response(self, response: str) -> str:
        """清理GPT响应，移除markdown标记等"""
        # 移除markdown代码块标记
        if response.startswith("```"):
            lines = response.split('\n')
            # 移除第一个和最后一个代码块标记
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            response = '\n'.join(lines)
        
        # 移除lisp标记
        response = response.replace("```lisp", "").replace("```lsp", "")
        
        return response.strip()
    
