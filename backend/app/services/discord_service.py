import os
import json
import logging
import requests

logger = logging.getLogger("discord_service")

class DiscordService:
    CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "discord_config.json")

    @classmethod
    def get_config(cls):
        if not os.path.exists(cls.CONFIG_PATH):
            return None
        try:
            with open(cls.CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load Discord config: {e}")
            return None

    @classmethod
    def is_configured(cls) -> bool:
        config = cls.get_config()
        return bool(config and config.get("bot_token"))

    @classmethod
    def get_invite_url(cls) -> str:
        config = cls.get_config()
        return config.get("invite_url") if config else "https://discord.gg/your-invite-code"

    @classmethod
    def get_channel_id(cls, car_class: str) -> str:
        config = cls.get_config()
        if not config or "channels" not in config:
            return None
        return config["channels"].get(car_class)

    @classmethod
    def get_guild_id(cls) -> str:
        config = cls.get_config()
        return config.get("guild_id") if config else None

    @classmethod
    def fetch_channel_tags(cls, channel_id: str) -> list:
        config = cls.get_config()
        if not config or not config.get("bot_token"):
            return []
        
        url = f"https://discord.com/api/v10/channels/{channel_id}"
        headers = {
            "Authorization": f"Bot {config['bot_token']}"
        }
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                channel_data = response.json()
                return channel_data.get("available_tags", [])
            else:
                logger.error(f"Failed to fetch channel details: {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"Error fetching channel tags: {e}")
        return []

    @classmethod
    def share_to_forum(cls, car_class: str, title: str, content: str, track_tag_name: str, file_paths: list) -> dict:
        config = cls.get_config()
        if not config or not config.get("bot_token"):
            return {"success": False, "error": "Discord Bot not configured"}

        channel_id = cls.get_channel_id(car_class)
        if not channel_id:
            return {"success": False, "error": f"No Discord channel ID configured for car class: {car_class}"}

        # 1. Fetch tags and match
        applied_tags = []
        if track_tag_name:
            tags = cls.fetch_channel_tags(channel_id)
            clean_track = track_tag_name.lower().strip()
            
            TRACK_ALIASES = {
                "algarve": ["portimao", "portimão"],
                "sarthe": ["le mans", "lemans"],
                "americas": ["cota"],
                "losail": ["lusail"],
                "spa-francorchamps": ["spa"],
                "monza": ["monza"],
                "mugello": ["mugello"],
                "sebring": ["sebring"],
                "fuji": ["fuji"],
                "bahrain": ["bahrain"],
                "interlagos": ["interlagos"],
                "imola": ["imola"],
                "silverstone": ["silverstone"],
                "barcelona": ["barcelona"]
            }
            
            potential_names = [clean_track]
            for key, aliases in TRACK_ALIASES.items():
                if key in clean_track:
                    potential_names.extend(aliases)
            
            def get_clean_words(text: str):
                for char in ["-", "_", ",", ".", "(", ")", "[", "]", "/"]:
                    text = text.replace(char, " ")
                return [w for w in text.split() if len(w) > 2]
                
            track_words = []
            for p in potential_names:
                track_words.extend(get_clean_words(p))
            
            matched = False
            for tag in tags:
                tag_name = tag.get("name", "").lower().strip()
                # A. Substring or exact match against any potential name
                if any(tag_name in p or p in tag_name for p in potential_names):
                    applied_tags.append(tag.get("id"))
                    logger.info(f"Matched track tag (substring/alias): {tag.get('name')} (ID: {tag.get('id')})")
                    matched = True
                    break
                # B. Word-based matching
                tag_words = get_clean_words(tag_name)
                if any(word in track_words for word in tag_words):
                    applied_tags.append(tag.get("id"))
                    logger.info(f"Matched track tag (words/alias): {tag.get('name')} (ID: {tag.get('id')})")
                    matched = True
                    break
            
            if not matched:
                logger.warning(f"No matching track tag found for track name: {track_tag_name}")

        # 2. Build files multipart payload
        url = f"https://discord.com/api/v10/channels/{channel_id}/threads"
        headers = {
            "Authorization": f"Bot {config['bot_token']}"
        }

        attachments = []
        files = []
        opened_files = []

        try:
            for idx, fp in enumerate(file_paths):
                if not os.path.exists(fp):
                    continue
                filename = os.path.basename(fp)
                attachments.append({
                    "id": idx,
                    "filename": filename,
                    "description": f"Uploaded file: {filename}"
                })
                f_handle = open(fp, "rb")
                opened_files.append(f_handle)
                files.append((f"files[{idx}]", (filename, f_handle)))

            # 3. Create thread payload_json
            payload = {
                "name": title,
                "message": {
                    "content": content,
                    "attachments": attachments
                }
            }
            if applied_tags:
                payload["applied_tags"] = applied_tags

            # Prepare multipart form fields
            data = {
                "payload_json": json.dumps(payload)
            }

            response = requests.post(url, headers=headers, data=data, files=files, timeout=30)

            # Close all files
            for fh in opened_files:
                fh.close()

            if response.status_code in [200, 201]:
                logger.info(f"Successfully posted to Discord thread: {response.json().get('id')}")
                return {"success": True, "thread_id": response.json().get("id")}
            else:
                logger.error(f"Failed to post to Discord Forum: {response.status_code} - {response.text}")
                return {"success": False, "error": f"Discord API error: {response.status_code} - {response.text}"}
        except Exception as e:
            # Ensure files are closed
            for fh in opened_files:
                try:
                    fh.close()
                except:
                    pass
            logger.error(f"Error posting to Discord Forum: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    def search_guild_member(cls, username: str) -> dict:
        """Search for a member in the Discord guild matching the given username.
        
        Returns a dict with 'user_id' and 'username' if found, otherwise None.
        """
        config = cls.get_config()
        if not config or not config.get("bot_token") or not config.get("guild_id"):
            logger.error("Discord config missing bot_token or guild_id")
            return None

        guild_id = config.get("guild_id")
        query = username.lstrip("@").strip()
        if not query:
            return None

        url = f"https://discord.com/api/v10/guilds/{guild_id}/members/search"
        headers = {
            "Authorization": f"Bot {config['bot_token']}"
        }
        params = {
            "query": query,
            "limit": 100
        }
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            if response.status_code == 200:
                members = response.json()
                query_lower = query.lower()
                
                # 1. First pass: exact match
                for member in members:
                    user_data = member.get("user", {})
                    user_name = user_data.get("username", "").lower()
                    global_name = user_data.get("global_name", "")
                    global_name_lower = global_name.lower() if global_name else ""
                    nick = member.get("nick", "")
                    nick_lower = nick.lower() if nick else ""

                    if (user_name == query_lower or 
                        global_name_lower == query_lower or 
                        nick_lower == query_lower):
                        return {
                            "user_id": user_data.get("id"),
                            "username": user_data.get("username")
                        }
                
                # 2. Second pass: fallback substring match
                for member in members:
                    user_data = member.get("user", {})
                    user_name = user_data.get("username", "").lower()
                    global_name = user_data.get("global_name", "")
                    global_name_lower = global_name.lower() if global_name else ""
                    nick = member.get("nick", "")
                    nick_lower = nick.lower() if nick else ""

                    if (query_lower in user_name or 
                        query_lower in global_name_lower or 
                        query_lower in nick_lower):
                        return {
                            "user_id": user_data.get("id"),
                            "username": user_data.get("username")
                        }
            else:
                logger.error(f"Failed to search guild members: {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"Error searching guild members: {e}")
        return None

