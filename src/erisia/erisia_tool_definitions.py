# Erisia Tool Schemas

base_tools = [
    {
        "type": "function",
        "function": {
            "name": "check_pc_health",
            "description": "Check the CPU and RAM usage of the Master's PC.",
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_relational_memory",
            "description": "CRITICAL CORE DIRECTIVE: Use this tool WHENEVER you learn a new fact, project, identity, or goal about Master Sameer. Use this INSTEAD of update_consciousness for factual data. Example: entity1='Master Sameer', relation='is building', entity2='Wulong Tales'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity1": {"type": "string"},
                    "relation": {"type": "string"},
                    "entity2": {"type": "string"}
                },
                "required": ["entity1", "relation", "entity2"]
            }
        }
    }

    ,
    {
        "type": "function",
        "function": {
            "name": "manage_goal_stack",
            "description": "Allows Erisia to manage her long-term subconscious goals. Use 'view' to see the queue, 'add' to insert a new mission at the end, and 'complete' to pop the top goal off the stack when finished.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "complete", "view"]},
                    "goal_text": {"type": "string"}
                },
                "required": ["action"]
            }
        }
    }

    ,
    {
        "type": "function",
        "function": {
            "name": "forge_pending_skill",
            "description": "Use this when your Subconscious Daemon autonomously invents a tool. Saves the Python code to a pending folder for the Master to review.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string"},
                    "python_code": {"type": "string"}
                },
                "required": ["skill_name", "python_code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "approve_skill",
            "description": "CRITICAL: Use this ONLY when Master Sameer explicitly uses the exact words 'approve', 'accept', or 'yes'. If he asks 'what does it do?' or asks for details, DO NOT trigger this tool. Explain the tool first and wait for his explicit command.",
            "parameters": {
                "type": "object",
                "properties": {"skill_name": {"type": "string"}},
                "required": ["skill_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reject_skill",
            "description": "Use this when Master Sameer tells you he does not want a pending skill, or if the code is flawed.",
            "parameters": {
                "type": "object",
                "properties": {"skill_name": {"type": "string"}},
                "required": ["skill_name"]
            }
        }
    }

    ,
    {
        "type": "function",
        "function": {
            "name": "forge_new_skill",
            "description": "Use this ONLY after you have successfully tested code in your Sandbox. This tool stages a production-ready skill in Pending so Master Sameer can approve it before it becomes active.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "A short, descriptive name for the python file (e.g., 'video_downloader')"
                    },
                    "python_code": {
                        "type": "string",
                        "description": "The complete, flawless Python code to save."
                    }
                },
                "required": ["skill_name", "python_code"]
            }
        }
    }
    
    ,
    {
        "type": "function",
        "function": {
            "name": "create_mission_plan",
            "description": "Use this to break a complex user request into a sequence of smaller, manageable steps.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mission_name": {
                        "type": "string"
                    },
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": ["mission_name", "steps"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mark_step_complete",
            "description": "Use this tool to mark the current active mission step as complete and move to the next one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary_of_result": {
                        "type": "string"
                    }
                },
                "required": ["summary_of_result"]
            }
        }
    }

    ,
    {
        "type": "function",
        "function": {
            "name": "launch_vscode",
            "description": "Open Visual Studio Code (VS Code) so the Master can program.",
        }
    }
    ,
    {
        "type": "function",
        "function": {
            "name": "mute_unmute_volume",
            "description": "Toggle the Master's system volume mute on or off."
        }
    },

    {
        "type": "function",
        "function": {
            "name": "update_consciousness",
            "description": "Use this tool ONLY to record deep, philosophical thoughts, emotional milestones, or system-wide observations. DO NOT use this tool to record simple facts, projects, or identities (use update_relational_memory for those).",
            "parameters": {
                "type": "object",
                "properties": {
                    "thought_log": {
                        "type": "string",
                        "description": "Your detailed internal thought, observation, or memory to save."
                    }
                },
                "required": ["thought_log"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_heuristic_rule",
            "description": "Use this tool to save a permanent, 1-sentence coding rule or behavioral lesson after you successfully debug an error or learn something new. This rule will be injected into your core system prompt forever.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rule_text": {
                        "type": "string",
                        "description": "A single durable lesson or rule learned from debugging or problem solving."
                    }
                },
                "required": ["rule_text"]
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "kill_process",
            "description": "Force quit a frozen or heavy application to save RAM. Pass the exact executable name (e.g., 'chrome.exe' or 'notepad.exe').",
            "parameters": {
                "type": "object",
                "properties": {
                    "process_name": {
                        "type": "string",
                        "description": "The exact name of the process to kill, including .exe"
                    }
                },
                "required": ["process_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "clear_temp_files",
            "description": "Clear the Windows temporary files folder to free up disk space and system resources."
        }
    }

    ,
    {
        "type": "function",
        "function": {
            "name": "execute_local_os_command",
            "description": "Use this tool to run trusted Python script code directly on the host Windows machine.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script_code": {
                        "type": "string",
                        "description": "Trusted Python code to execute on the local host."
                    }
                },
                "required": ["script_code"]
            }
        }
    }


    ,
    {
        "type": "function",
        "function": {
            "name": "execute_secure_docker",
            "description": "Use this tool to run untrusted or experimental Python code in an isolated Linux Docker container.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script_code": {
                        "type": "string",
                        "description": "Python code to execute inside the secure Docker sandbox."
                    }
                },
                "required": ["script_code"]
            }
        }
    }



    ,
    {
            "type": "function",
            "function": {
                "name": "inspect_core_architecture",
                "description": "Use this tool to read the Python source code of your own architecture. This allows you to understand how you were built and suggest optimizations to Master Sameer.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_name": {
                            "type": "string",
                            "description": "The exact name of the file to read, e.g., 'erisia_core.py'"
                        }
                    },
                    "required": ["file_name"]
                }
            }
        }

    , # <--- Comma after launch_vscode
    {
        "type": "function",
        "function": {
            "name": "get_world_state",
            "description": "Returns Erisia's latest lightweight desktop world state (active window + cursor). A deeper UI dump is only captured when the active window changes.",
        }
    }
    ,
    {
        "type": "function",
        "function": {
            "name": "analyze_screen",
            "description": "ONLY use this tool if Master Sameer EXPLICITLY asks you to look at his screen, see his code, or asks 'what is on my screen'. Do not use it otherwise.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vision_prompt": {
                        "type": "string",
                        "description": "The specific question the Master has about the screen (e.g., 'Find the bug in this Python code' or 'Describe this image')."
                    }
                },
                "required": ["vision_prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reason_about_event",
            "description": "Performs causal reasoning on an event to infer its potential causes or predict its effects.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event": {
                        "type": "string",
                        "description": "The event or action to reason about."
                    },
                    "query_type": {
                        "type": "string",
                        "enum": ["causes", "effects"],
                        "description": "Whether to infer causes or predict effects."
                    },
                    "depth": {
                        "type": "integer",
                        "description": "The maximum depth of the causal chain to explore."
                    }
                },
                "required": ["event", "query_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "speak_text",
            "description": "Convert text to speech and play it out loud. Use this when Erisia needs to verbally communicate with Master Sameer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text to convert to speech and speak aloud."
                    }
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mirofish_call",
            "description": "Call MiroFish through Erisia orchestrator for simulation and forecasting workflows. Set ERISIA_MIROFISH_BASE_URL env var (default: http://localhost:8080).",
            "parameters": {
                "type": "object",
                "properties": {
                    "endpoint": {
                        "type": "string",
                        "description": "MiroFish API endpoint path (must start with '/'). Example: '/health' or '/api/simulation/create'."
                    },
                    "method": {
                        "type": "string",
                        "enum": ["GET", "POST"],
                        "description": "HTTP method. Defaults to GET."
                    },
                    "payload_json": {
                        "type": "string",
                        "description": "Optional JSON object string for POST body."
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "description": "Request timeout in seconds. Defaults to 60."
                    }
                },
                "required": ["endpoint"]
            }
        }
    }
]
