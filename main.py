import discord
from discord.ext import commands
from discord.ui import View, Button, Select, Modal, TextInput
import asyncio
from datetime import datetime, timedelta
import io

# Настройки
import os

# Токен берётся из переменной окружения DISCORD_TOKEN (настраивается в панели хостинга)
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Переменная окружения DISCORD_TOKEN не задана! Добавь её в панели хостинга.")
LOG_CHANNEL_ID = 1514694347441049601  # Канал для логов
TICKETS_CHANNEL_ID = 1514694429796073522  # Канал для тикетов
TICKETS_STORAGE_CHANNEL_ID = 1514693865096085595  # Хранилище для данных тикетов
VOICE_STORAGE_CHANNEL_ID = 1514693580235477012  # Хранилище для голосовых комнат
ARCHIVE_CHANNEL_ID = 1514694263873474703  # Архив для мутов и банов
BAN_CHANNEL_1 = 1514694051914449039  # Канал 1 для забаненных
BAN_CHANNEL_2 = 1514694171879805110  # Канал 2 для забаненных

# ID ролей для наказаний (если None, будут созданы автоматически)
MUTE_ROLE_ID = None  # Роль для мута (если None - создастся 'Блокировка чата')
BAN_ROLE_ID = None  # Роль для бана (если None - создастся 'Пользователь заблокирован')

# Шаблонный голосовой канал и категория
TEMPLATE_VOICE_CHANNEL_NAME = "создать войс"
VOICE_CATEGORY_ID = None  # ID категории где находится "создать войс" канал

# Интенты
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# Хранилище для созданных голосовых каналов
created_channels = {}

# Хранилище для данных тикетов
ticket_data = {}

# История принявших тикет (для закрытия)
accepted_by_users = {}

# Хранилище активных мутов и банов
active_mutes = {}
active_bans = {}

# Список ролей для мута и бана (кэшируем при старте)
mute_role_name = "Блокировка чата"
ban_role_name = "Пользователь заблокирован"

# История всех наказаний
punishment_history = []


# ==================== СОБЫТИЯ ====================

@bot.event
async def on_ready():
    print(f"Бот {bot.user} запущен!")
    print(f"ID бота: {bot.user.id}")
    print("Бот готов к работе!")
    # Регистрируем вечные панели — кнопки работают даже после перезапуска бота
    bot.add_view(ModerationPanelView())
    bot.add_view(SupportPanelView())
    print("✅ Вечные панели зарегистрированы")
    # Восстанавливаем таймеры для активных мутов/банов после перезапуска
    # (active_mutes и active_bans хранятся в памяти, при перезапуске сбрасываются)
    print("⚠️ Внимание: активные муты/баны сбрасываются при перезапуске бота!")


@bot.event
async def on_guild_channel_create(channel):
    """При создании нового канала — скрываем его от забаненных пользователей через роль"""
    # Роль бана уже имеет deny view_channel=False на @everyone через overwrites всех каналов,
    # но надёжнее явно прописывать запрет для роли бана на новом канале
    guild = channel.guild
    ban_role = discord.utils.get(guild.roles, name="Пользователь заблокирован")
    if ban_role and isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel)):
        # Проверяем, есть ли активные баны
        if active_bans:
            try:
                await channel.set_permissions(ban_role, view_channel=False)
            except Exception as e:
                print(f"Не удалось скрыть новый канал от роли бана: {e}")


@bot.event
async def on_voice_state_update(member, before, after):
    """Обработка изменения голосового состояния пользователя"""
    # Проверяем, если пользователь в муте, отключаем микрофон
    if after.channel is not None:
        if member.id in active_mutes:
            try:
                await member.edit(mute=True)
            except:
                pass

    # Проверяем выход пользователя из голосового канала
    if before.channel is not None and after.channel is None:
        channel_id = before.channel.id
        channel_key = None

        for key, data in created_channels.items():
            if data["voice"] == channel_id:
                channel_key = key
                break

        if channel_key and channel_key in created_channels:
            voice_channel = before.channel
            if len(voice_channel.members) == 0:
                try:
                    await voice_channel.delete()
                    del created_channels[channel_key]
                    print(f"Голосовой канал {voice_channel.name} удалён (пуст)")

                    # Сохраняем в хранилище
                    storage_channel = bot.get_channel(VOICE_STORAGE_CHANNEL_ID)
                    if storage_channel:
                        await storage_channel.send(f"🗑️ Удалён канал: {voice_channel.name}")
                except:
                    pass

    # Проверяем перемещение пользователя между голосовыми каналами
    if before.channel is not None and after.channel is not None and before.channel != after.channel:
        channel_id = before.channel.id
        channel_key = None

        for key, data in created_channels.items():
            if data["voice"] == channel_id:
                channel_key = key
                break

        if channel_key and channel_key in created_channels:
            voice_channel = before.channel
            if len(voice_channel.members) == 0:
                try:
                    await voice_channel.delete()
                    del created_channels[channel_key]
                    print(f"Голосовой канал {voice_channel.name} удалён (пуст)")

                    # Сохраняем в хранилище
                    storage_channel = bot.get_channel(VOICE_STORAGE_CHANNEL_ID)
                    if storage_channel:
                        await storage_channel.send(f"🗑️ Удалён канал: {voice_channel.name}")
                except:
                    pass

        # Проверяем вход в шаблонный канал при перемещении
        if after.channel.name.lower() == TEMPLATE_VOICE_CHANNEL_NAME.lower():
            await create_voice_room(member, after.channel)

    if before.channel is None and after.channel is not None:
        # Пользователь зашёл в голосовой канал
        if after.channel.name.lower() == TEMPLATE_VOICE_CHANNEL_NAME.lower():
            await create_voice_room(member, after.channel)


@bot.event
async def on_guild_channel_delete(channel):
    """Удаление канала из хранилища при удалении"""
    channel_key = None
    for key, data in created_channels.items():
        if data["voice"] == channel.id:
            channel_key = key
            break

    if channel_key:
        del created_channels[channel_key]


# ==================== ГОЛОСОВЫЕ КОМНАТЫ ====================

class LimitModal(discord.ui.Modal):
    def __init__(self, voice_channel_id: int, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.voice_channel_id = voice_channel_id
        self.add_item(discord.ui.TextInput(
            label="Лимит участников",
            placeholder="Введите число (например, 10)",
            min_length=1,
            max_length=3
        ))

    async def on_submit(self, interaction: discord.Interaction):
        limit_value = int(self.children[0].value)
        if limit_value < 0 or limit_value > 99:
            await interaction.response.send_message("❌ Лимит должен быть от 0 до 99.", ephemeral=True)
            return

        voice_channel = interaction.guild.get_channel(self.voice_channel_id)
        if voice_channel:
            await voice_channel.edit(user_limit=limit_value)
            await interaction.response.send_message(f"✅ Лимит установлен: {limit_value} участников.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Голосовой канал не найден.", ephemeral=True)


class BanSelect(discord.ui.Select):
    def __init__(self, voice_channel_id: int, members: list, owner_id: int, guild: discord.Guild):
        options = []
        for member in guild.members:
            if not member.bot and member.id != owner_id:
                options.append(discord.SelectOption(
                    label=member.display_name,
                    value=str(member.id),
                    description=f"{member.name}#{member.discriminator}"
                ))
        if not options:
            options = [discord.SelectOption(label="Нет других участников", value="0",
                                            description="В комнате только вы - владелец")]

        super().__init__(
            placeholder="Выберите пользователя для бана...",
            options=options,
            custom_id="ban_select"
        )
        self.voice_channel_id = voice_channel_id
        self.owner_id = owner_id
        self.guild = guild

    async def callback(self, interaction: discord.Interaction):
        voice_channel = interaction.guild.get_channel(self.voice_channel_id)
        if not voice_channel:
            await interaction.response.send_message("❌ Голосовой канал не найден.", ephemeral=True)
            return

        user_id = int(interaction.data['values'][0])
        user = self.guild.get_member(user_id)
        if not user:
            await interaction.response.send_message("❌ Пользователь не найден.", ephemeral=True)
            return

        overwrites = voice_channel.overwrites
        if user in overwrites and not overwrites[user].connect:
            del overwrites[user]
            await voice_channel.edit(overwrites=overwrites)
            await interaction.response.send_message(f"✅ Пользователь {user.mention} разбанен.", ephemeral=True)
        else:
            overwrites[user] = discord.PermissionOverwrite(connect=False)
            await voice_channel.edit(overwrites=overwrites)

            if user.voice and user.voice.channel == voice_channel:
                await user.move_to(None)

            await interaction.response.send_message(f"✅ Пользователь {user.mention} забанен в голосовом канале.",
                                                    ephemeral=True)


class MuteSelect(discord.ui.Select):
    def __init__(self, voice_channel_id: int, members: list, owner_id: int, guild: discord.Guild):
        options = []
        for member in guild.members:
            if not member.bot and member.id != owner_id:
                options.append(discord.SelectOption(
                    label=member.display_name,
                    value=str(member.id),
                    description=f"{member.name}#{member.discriminator}"
                ))
        if not options:
            options = [discord.SelectOption(label="Нет других участников", value="0",
                                            description="В комнате только вы - владелец")]

        super().__init__(
            placeholder="Выберите пользователя для мута...",
            options=options,
            custom_id="mute_select"
        )
        self.voice_channel_id = voice_channel_id
        self.owner_id = owner_id
        self.guild = guild

    async def callback(self, interaction: discord.Interaction):
        voice_channel = interaction.guild.get_channel(self.voice_channel_id)
        if not voice_channel:
            await interaction.response.send_message("❌ Голосовой канал не найден.", ephemeral=True)
            return

        user_id = int(interaction.data['values'][0])
        user = self.guild.get_member(user_id)
        if not user:
            await interaction.response.send_message("❌ Пользователь не найден.", ephemeral=True)
            return

        overwrites = voice_channel.overwrites
        if user in overwrites and not overwrites[user].speak:
            del overwrites[user]
            await voice_channel.edit(overwrites=overwrites)
            await interaction.response.send_message(f"✅ Пользователь {user.mention} размучен.", ephemeral=True)
        else:
            overwrites[user] = discord.PermissionOverwrite(speak=False)
            await voice_channel.edit(overwrites=overwrites)

            if user.voice and user.voice.channel == voice_channel:
                await user.move_to(None)

            await interaction.response.send_message(
                f"✅ Пользователь {user.mention} замучен в голосовом канале (микрофон отключен).", ephemeral=True)


class VideoSelect(discord.ui.Select):
    def __init__(self, voice_channel_id: int, members: list, owner_id: int, guild: discord.Guild):
        options = []
        for member in guild.members:
            if not member.bot and member.id != owner_id:
                options.append(discord.SelectOption(
                    label=member.display_name,
                    value=str(member.id),
                    description=f"{member.name}#{member.discriminator}"
                ))
        if not options:
            options = [discord.SelectOption(label="Нет других участников", value="0",
                                            description="В комнате только вы - владелец")]

        super().__init__(
            placeholder="Выберите пользователя для отключения видео...",
            options=options,
            custom_id="video_select"
        )
        self.voice_channel_id = voice_channel_id
        self.owner_id = owner_id
        self.guild = guild

    async def callback(self, interaction: discord.Interaction):
        voice_channel = interaction.guild.get_channel(self.voice_channel_id)
        if not voice_channel:
            await interaction.response.send_message("❌ Голосовой канал не найден.", ephemeral=True)
            return

        user_id = int(interaction.data['values'][0])
        user = self.guild.get_member(user_id)
        if not user:
            await interaction.response.send_message("❌ Пользователь не найден.", ephemeral=True)
            return

        overwrites = voice_channel.overwrites
        if user in overwrites and not overwrites[user].stream:
            del overwrites[user]
            await voice_channel.edit(overwrites=overwrites)
            await interaction.response.send_message(f"✅ У пользователя {user.mention} включено видео.", ephemeral=True)
        else:
            overwrites[user] = discord.PermissionOverwrite(stream=False)
            await voice_channel.edit(overwrites=overwrites)
            await interaction.response.send_message(
                f"✅ У п��льзователя {user.mention} отключено видео в голосовом канале.", ephemeral=True)


class VoiceChannelManagementView(discord.ui.View):
    """Класс для управления голосовым каналом"""

    def __init__(self, owner_id: int, voice_channel_id: int, members: list):
        super().__init__(timeout=None)
        self.owner_id = owner_id
        self.voice_channel_id = voice_channel_id
        self.members = members

    @discord.ui.button(label="Лимит", style=discord.ButtonStyle.primary, emoji="👤")
    async def set_limit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Только владелец комнаты может изменить лимит.", ephemeral=True)
            return

        await interaction.response.send_modal(LimitModal(self.voice_channel_id, title="Установить лимит участников"))

    @discord.ui.button(label="Закрыть комнату", style=discord.ButtonStyle.danger, emoji="🔒")
    async def close_room(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Только владелец комнаты может закрыть комнату.", ephemeral=True)
            return

        voice_channel = interaction.guild.get_channel(self.voice_channel_id)
        if not voice_channel:
            await interaction.response.send_message("❌ Голосовой канал не найден.", ephemeral=True)
            return

        overwrites = voice_channel.overwrites
        members_to_remove = [member for member in voice_channel.overwrites.keys() if
                             member != interaction.guild.me and member.id != self.owner_id]
        for member in members_to_remove:
            del overwrites[member]
        overwrites[interaction.guild.default_role] = discord.PermissionOverwrite(view_channel=False)

        await voice_channel.edit(overwrites=overwrites)
        await interaction.response.send_message("✅ Комната закрыта! Теперь только вы можете видеть этот канал.",
                                                ephemeral=True)

    @discord.ui.button(label="Открыть комнату", style=discord.ButtonStyle.success, emoji="🔓")
    async def open_room(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Только владелец комнаты может открыть комнату.", ephemeral=True)
            return

        voice_channel = interaction.guild.get_channel(self.voice_channel_id)
        if not voice_channel:
            await interaction.response.send_message("❌ Голосовой канал не найден.", ephemeral=True)
            return

        overwrites = voice_channel.overwrites
        members_to_remove = [member for member in voice_channel.overwrites.keys() if
                             member != interaction.guild.me and member.id != self.owner_id]
        for member in members_to_remove:
            del overwrites[member]
        overwrites[interaction.user] = discord.PermissionOverwrite(view_channel=True, connect=True)
        overwrites[interaction.guild.default_role] = discord.PermissionOverwrite(view_channel=True, connect=True)
        await voice_channel.edit(overwrites=overwrites)

        await interaction.response.send_message("✅ Комната открыта! Теперь другие пользователи могут подключиться.",
                                                ephemeral=True)

    @discord.ui.button(label="Забанить", style=discord.ButtonStyle.danger, emoji="🚫")
    async def show_ban_select(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Только владелец комнаты может банить пользователей.",
                                                    ephemeral=True)
            return

        voice_channel = interaction.guild.get_channel(self.voice_channel_id)
        if not voice_channel:
            await interaction.response.send_message("❌ Голосовой канал не найден.", ephemeral=True)
            return

        members = [m for m in voice_channel.members if m != interaction.guild.me]

        view = discord.ui.View()
        view.add_item(BanSelect(self.voice_channel_id, members, self.owner_id, interaction.guild))

        embed = discord.Embed(
            title="🚫 Забанить пользователя",
            description="Выберите пользователя из списка:",
            color=discord.Color.red()
        )

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Замутить", style=discord.ButtonStyle.danger, emoji="🔇")
    async def show_mute_select(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Только владелец комнаты может мутить пользователей.",
                                                    ephemeral=True)
            return

        voice_channel = interaction.guild.get_channel(self.voice_channel_id)
        if not voice_channel:
            await interaction.response.send_message("❌ Голосовой канал не найден.", ephemeral=True)
            return

        members = [m for m in voice_channel.members if m != interaction.guild.me]

        view = discord.ui.View()
        view.add_item(MuteSelect(self.voice_channel_id, members, self.owner_id, interaction.guild))

        embed = discord.Embed(
            title="🔇 Замутить пользователя",
            description="Выберите пользователя из списка:",
            color=discord.Color.red()
        )

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Отключить видео", style=discord.ButtonStyle.danger, emoji="📹")
    async def show_video_select(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Только владелец комнаты может отключать видео.", ephemeral=True)
            return

        voice_channel = interaction.guild.get_channel(self.voice_channel_id)
        if not voice_channel:
            await interaction.response.send_message("❌ Голосовой канал не найден.", ephemeral=True)
            return

        members = [m for m in voice_channel.members if m != interaction.guild.me]

        view = discord.ui.View()
        view.add_item(VideoSelect(self.voice_channel_id, members, self.owner_id, interaction.guild))

        embed = discord.Embed(
            title="📹 Отключить видео",
            description="Выберите пользователя из списка:",
            color=discord.Color.red()
        )

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def create_voice_room(member, template_channel):
    """Создание голосовой комнаты"""
    guild = member.guild

    # Используем существующую категорию шаблонного канала
    category = template_channel.category

    voice_channel = await guild.create_voice_channel(
        f"{member.name}'s Room",
        category=category
    )

    channel_key = f"{member.id}_{voice_channel.id}"
    created_channels[channel_key] = {
        "voice": voice_channel.id,
        "owner": member.id
    }

    # Сохраняем в хранилище
    storage_channel = bot.get_channel(VOICE_STORAGE_CHANNEL_ID)
    if storage_channel:
        await storage_channel.send(
            f"✅ Создана комната: {voice_channel.name} для {member.mention}\nID: {voice_channel.id}")

    overwrites = {
        member: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            manage_channels=True,
            mute_members=True,
            deafen_members=True,
            move_members=True
        )
    }

    try:
        await voice_channel.edit(overwrites=overwrites)
    except Exception as e:
        print(f"Ошибка при установке прав: {e}")

    # Если есть активные баны — скрываем новый канал от роли бана
    if active_bans:
        ban_role = discord.utils.get(guild.roles, name=ban_role_name)
        if ban_role:
            try:
                await voice_channel.set_permissions(ban_role, view_channel=False)
            except Exception as e:
                print(f"Не удалось скрыть войс от роли бана: {e}")

    try:
        await member.move_to(voice_channel)
    except Exception as e:
        print(f"Ошибка при перемещении пользователя: {e}")

    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        try:
            log_embed = discord.Embed(
                title="🔊 Создана новая голосовая комната",
                description=f"Создатель: {member.mention}\nГолосовой канал: {voice_channel.mention}",
                color=discord.Color.blue()
            )
            log_embed.add_field(name="ID голосового канала", value=voice_channel.id, inline=True)
            await log_channel.send(embed=log_embed)
        except Exception as e:
            print(f"Ошибка при публикации в лог: {e}")

    try:
        embed = discord.Embed(
            title="🎤 Ваша голосовая комната",
            description=f"Комната создана! **{member.name}** - вы владелец этой комнаты.\n\n"
                        "Используйте кнопки ниже для управления каналом:",
            color=discord.Color.orange()
        )
        embed.add_field(name="Голосовой канал", value=voice_channel.mention, inline=True)
        embed.set_thumbnail(url="https://i.ibb.co/ksFjHrk8/channels4-profile.jpg")

        members = list(set(voice_channel.members) | {member})
        members = [m for m in members if m != bot.user]
        print(f"Участники канала {voice_channel.name}: {[m.name for m in members]}")
        print(f"Владелец: {member.name} (ID: {member.id})")

        management_view = VoiceChannelManagementView(member.id, voice_channel.id, members)
        await voice_channel.send(embed=embed, view=management_view)
    except Exception as e:
        print(f"Ошибка при отправке сообщения в голосовой канал: {e}")

    print(f"Создана голосовая комната для {member.name}: {voice_channel.name}")


# ==================== ТИКЕТЫ ====================

class TicketModal(Modal):
    def __init__(self, category):
        super().__init__(title=f"Новый тикет: {category}")
        self.category = category

        if category == "Пожалаться на участника":
            self.add_item(
                TextInput(label="Имя/ID нарушителя", placeholder="Введите имя участника или его @", required=True,
                          custom_id="violator"))
            self.add_item(TextInput(label="Описание нарушения", placeholder="Опишите что произошло",
                                    style=discord.TextStyle.paragraph, required=True, custom_id="description"))
            self.add_item(
                TextInput(label="Доказательства", placeholder="Скриншоты, ссылки", style=discord.TextStyle.short,
                          required=False, custom_id="evidence"))
        elif category == "Заявка на модератора":
            self.add_item(TextInput(label="Ваш никнейм", placeholder="Введите ваш Discord ник", required=True,
                                    custom_id="nickname"))
            self.add_item(TextInput(label="Возраст", placeholder="Сколько вам лет?", required=True, custom_id="age"))
            self.add_item(TextInput(label="Опыт модерации", placeholder="Были ли у вас опыт модерации?", required=True,
                                    custom_id="experience"))
            self.add_item(TextInput(label="Почему мы должны вас выбрать?", placeholder="Ваши сильные стороны",
                                    style=discord.TextStyle.paragraph, required=True, custom_id="why"))
        elif category == "Обжаловать блокировку":
            self.add_item(TextInput(label="Ваш никнейм при блокировке", placeholder="Введите ваш ник", required=True,
                                    custom_id="blocked_nickname"))
            self.add_item(
                TextInput(label="Причина блокировки", placeholder="Какая причина была указана?", required=True,
                          custom_id="ban_reason"))
            self.add_item(TextInput(label="Обоснование снятия", placeholder="Почему блокировку нужно снять",
                                    style=discord.TextStyle.paragraph, required=True, custom_id="justification"))
        else:
            self.add_item(TextInput(label="Тема обращения", placeholder="Кратко опишите тему", required=True,
                                    custom_id="subject"))
            self.add_item(TextInput(label="Описание проблемы", placeholder="Раскройте подробнее ваш вопрос",
                                    style=discord.TextStyle.paragraph, required=True, custom_id="description"))

    async def on_submit(self, interaction: discord.Interaction):
        values = {}
        for child in self.children:
            values[child.custom_id] = child.value

        await interaction.response.send_message("✅ Ваш запрос успешно отправлен!", ephemeral=True)

        try:
            channel = await bot.fetch_channel(TICKETS_CHANNEL_ID)
            if channel:
                embed = discord.Embed(
                    title="📋 Новый тикет",
                    description=f"**Категория:** {self.category}",
                    color=0xFFA500
                )
                embed.set_author(name=f"{interaction.user}", icon_url=interaction.user.display_avatar.url)

                if self.category == "Пожалаться на участника":
                    embed.add_field(name="👤 Нарушитель", value=values.get("violator", "Не указано"), inline=False)
                    embed.add_field(name="📝 Описание", value=values.get("description", "Не указано"), inline=False)
                    if values.get("evidence"):
                        embed.add_field(name="🖼️ Доказательства", value=values["evidence"], inline=False)
                elif self.category == "Заявка на модератора":
                    embed.add_field(name="👤 Никнейм", value=values.get("nickname", "Не указано"), inline=True)
                    embed.add_field(name="🎂 Возраст", value=values.get("age", "Не указано"), inline=True)
                    embed.add_field(name="💼 Опыт", value=values.get("experience", "Не указано"), inline=False)
                    embed.add_field(name="💬 Обоснование", value=values.get("why", "Не указано"), inline=False)
                elif self.category == "Обжаловать блокировку":
                    embed.add_field(name="👤 Никнейм", value=values.get("blocked_nickname", "Не указано"), inline=True)
                    embed.add_field(name="🚫 Причина", value=values.get("ban_reason", "Не указано"), inline=False)
                    embed.add_field(name="✅ Обоснование", value=values.get("justification", "Не указано"), inline=False)
                else:
                    embed.add_field(name="🎯 Тема", value=values.get("subject", "Не указано"), inline=False)
                    embed.add_field(name="📋 Описание", value=values.get("description", "Не указано"), inline=False)

                embed.add_field(name="🔗 Пользователь", value=f"{interaction.user.mention}", inline=False)
                embed.set_footer(text=f"ID: {interaction.user.id} | {self.category}")

                view = SupportTicketView(interaction.user, self.category)
                msg = await channel.send(embed=embed, view=view)

                ticket_data[msg.id] = {
                    'user': interaction.user,
                    'category': self.category,
                    'author': interaction.user,
                    'channel_id': channel.id
                }

                # Сохраняем в хранилище тикетов
                storage_channel = bot.get_channel(TICKETS_STORAGE_CHANNEL_ID)
                if storage_channel:
                    await storage_channel.send(
                        f"📋 Новый тикет от {interaction.user.mention}\nКатегория: {self.category}\nID сообщения: {msg.id}")

                print(f"Сохранен тикет {msg.id}: {interaction.user}")
        except Exception as e:
            print(f"Ошибка при отправке в канал: {e}")
            import traceback
            traceback.print_exc()


class AcceptButton(Button):
    def __init__(self, user, category):
        super().__init__(label="✅ Принять", style=discord.ButtonStyle.green)
        self.user = user
        self.category = category

    async def callback(self, interaction):
        msg_id = interaction.message.id

        if msg_id not in accepted_by_users:
            accepted_by_users[msg_id] = []
        accepted_by_users[msg_id].append(interaction.user)

        ticket = ticket_data.get(msg_id)
        user = ticket['user'] if ticket else self.user
        category = ticket['category'] if ticket else self.category
        channel_id = ticket['channel_id'] if ticket else TICKETS_CHANNEL_ID

        try:
            channel = await bot.fetch_channel(channel_id)
            msg = await channel.fetch_message(msg_id)
            await msg.edit(view=None)
        except Exception as e:
            print(f"Ошибка при редактировании сообщения: {e}")

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("❌ Эта команда только для серверов!", ephemeral=True)
            return

        category_obj = discord.utils.get(guild.categories, name="Тикеты")

        if not category_obj:
            category_obj = await guild.create_category("Тикеты")

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True)
        }

        ticket_channel_name = f"ticket-{user.name.lower().replace(' ', '-')}"

        try:
            ticket_channel = await guild.create_text_channel(
                ticket_channel_name,
                category=category_obj,
                overwrites=overwrites
            )

            await interaction.response.send_message(f"✅ Канал создан: {ticket_channel.mention}", ephemeral=True)

            embed = discord.Embed(
                title="📋 Тикет принят",
                description=f"**Категория:** {category}\n\nДобро пожаловать в приватный канал поддержки!",
                color=0xFFA500
            )
            embed.add_field(name="Пользователь", value=user.mention, inline=True)
            embed.add_field(name="Модератор", value=interaction.user.mention, inline=True)
            embed.set_footer(text=f"ID пользователя: {user.id}")

            msg_in_channel = await ticket_channel.send(embed=embed)

            await ticket_channel.send(view=TicketManagementView(user, category, msg_in_channel.id, ticket_channel.id))

            # Сохраняем в хранилище
            storage_channel = bot.get_channel(TICKETS_STORAGE_CHANNEL_ID)
            if storage_channel:
                await storage_channel.send(
                    f"✅ Тикет принят {interaction.user.mention}\nКанал: {ticket_channel.mention}")

            try:
                await user.send(f"✅ Ваш тикет был принят! Канал создан: {ticket_channel.mention}")
            except:
                pass

        except Exception as e:
            print(f"Ошибка пр�� создании канала: {e}")
            import traceback
            traceback.print_exc()
            await interaction.response.send_message(f"❌ Ошибка при создании канала: {e}", ephemeral=True)


class DeclineButton(Button):
    def __init__(self, user, category):
        super().__init__(label="❌ Отклонить", style=discord.ButtonStyle.red)
        self.user = user
        self.category = category

    async def callback(self, interaction):
        msg_id = interaction.message.id

        ticket = ticket_data.get(msg_id)
        user = ticket['user'] if ticket else self.user
        category = ticket['category'] if ticket else self.category
        channel_id = ticket['channel_id'] if ticket else TICKETS_CHANNEL_ID

        try:
            channel = await bot.fetch_channel(channel_id)
            msg = await channel.fetch_message(msg_id)
            await msg.edit(view=None)
        except Exception as e:
            print(f"Ошибка при редактировании сообщения: {e}")

        # Сохраняем в хранилище
        storage_channel = bot.get_channel(TICKETS_STORAGE_CHANNEL_ID)
        if storage_channel:
            await storage_channel.send(f"❌ Тикет отклонен {interaction.user.mention}")

        try:
            await user.send(f"❌ Ваш тикет \"{category}\" был отклонен модератором {interaction.user.mention}.")
        except:
            pass

        await interaction.response.send_message(f"❌ Тикет отклонен!", ephemeral=True)


class SupportTicketView(View):
    def __init__(self, user, category):
        super().__init__(timeout=None)
        self.add_item(AcceptButton(user, category))
        self.add_item(DeclineButton(user, category))


class CloseTicketButton(Button):
    def __init__(self, ticket_channel_id):
        super().__init__(label="🔒 Закрыть тикет", style=discord.ButtonStyle.danger)
        self.ticket_channel_id = ticket_channel_id

    async def callback(self, interaction: discord.Interaction):
        msg_id = interaction.message.id

        accepted_users = accepted_by_users.get(msg_id, [])
        is_accepting_user = interaction.user in accepted_users

        has_admin_role = discord.utils.get(interaction.guild.roles, name="Админ") in interaction.user.roles
        has_curator_role = discord.utils.get(interaction.guild.roles, name="Куратор") in interaction.user.roles
        has_moderator_role = discord.utils.get(interaction.guild.roles, name="Модератор") in interaction.user.roles
        has_helper_role = discord.utils.get(interaction.guild.roles, name="Хелпер") in interaction.user.roles

        if not has_admin_role and not has_curator_role and not has_moderator_role and not has_helper_role:
            await interaction.response.send_message("❌ У вас нет прав для управления тикетом!", ephemeral=True)
            return

        if not is_accepting_user and not has_admin_role and not has_curator_role:
            await interaction.response.send_message("❌ Только тот, кто принял тикет, может его закрыть!",
                                                    ephemeral=True)
            return

        # Получаем историю сообщений
        ticket_channel = interaction.guild.get_channel(self.ticket_channel_id)
        if ticket_channel:
            try:
                messages = []
                async for message in ticket_channel.history(limit=None, oldest_first=True):
                    timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
                    messages.append(f"[{timestamp}] {message.author}: {message.content}")

                # Создаём текстовый файл с историей
                history_text = "📋 ИСТОРИЯ ТИКЕТА\n"
                history_text += "=" * 50 + "\n\n"
                for msg in messages:
                    history_text += msg + "\n"

                # Отправляем файл в хранилище тикетов
                storage_channel = bot.get_channel(TICKETS_STORAGE_CHANNEL_ID)
                if storage_channel and messages:
                    file = discord.File(
                        io.StringIO(history_text),
                        filename=f"ticket_{self.ticket_channel_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                    )
                    await storage_channel.send(
                        f"🔒 **Тикет закрыт:** {interaction.channel.mention}\n**Модератор:** {interaction.user.mention}",
                        file=file
                    )
            except Exception as e:
                print(f"Ошибка при получении истории: {e}")

        await interaction.response.send_message("🔒 Тикет закрывается...", ephemeral=True)
        await interaction.channel.delete()


class InviteTicketButton(Button):
    def __init__(self, user, category):
        super().__init__(label="👤 Пригласить", style=discord.ButtonStyle.secondary)
        self.user = user
        self.category = category

    async def callback(self, interaction: discord.Interaction):
        has_admin_role = discord.utils.get(interaction.guild.roles, name="Админ") in interaction.user.roles
        has_curator_role = discord.utils.get(interaction.guild.roles, name="Куратор") in interaction.user.roles
        has_moderator_role = discord.utils.get(interaction.guild.roles, name="Модератор") in interaction.user.roles
        has_helper_role = discord.utils.get(interaction.guild.roles, name="Хелпер") in interaction.user.roles

        if not has_admin_role and not has_curator_role and not has_moderator_role and not has_helper_role:
            await interaction.response.send_message("❌ У вас нет прав для управления тикетом!", ephemeral=True)
            return

        allowed_roles = ["Модератор", "Куратор", "Админ", "Хелпер"]

        inviteable_members = []
        for member in interaction.guild.members:
            for role_name in allowed_roles:
                role = discord.utils.get(interaction.guild.roles, name=role_name)
                if role and role in member.roles and member != interaction.user:
                    inviteable_members.append(member)
                    break

        if not inviteable_members:
            await interaction.response.send_message("❌ Нет подходящих участников для приглашения!", ephemeral=True)
            return

        class InviteSelect(Select):
            def __init__(self):
                options = [discord.SelectOption(label=m.display_name, value=str(m.id)) for m in inviteable_members]
                super().__init__(placeholder="Выберите участника для приглашения...", options=options,
                                 custom_id="invite_member")

            async def callback(self, sel_interaction: discord.Interaction):
                member_id = int(self.values[0])
                member = interaction.guild.get_member(member_id)

                if not member:
                    await sel_interaction.response.send_message("❌ Участник не найден!", ephemeral=True)
                    return

                await interaction.channel.set_permissions(member, read_messages=True, send_messages=False,
                                                          attach_files=False)

                class ConfirmJoinView(View):
                    @discord.ui.button(label="✅ Принять приглашение", style=discord.ButtonStyle.green)
                    async def confirm_button(self, btn_interaction: discord.Interaction, button: Button):
                        if btn_interaction.user == member:
                            await interaction.channel.set_permissions(member, read_messages=True, send_messages=True,
                                                                      attach_files=True)
                            await btn_interaction.response.send_message(
                                "✅ Вы приняли приглашение и присоединились к тикету!", ephemeral=True)
                            await interaction.channel.send(
                                f"👤 {member.mention} присоединился к тикету после подтверждения")
                            await btn_interaction.message.delete()

                try:
                    await member.send(
                        f"👤 Вам пришло приглашение присоединиться к тикету: {interaction.channel.mention}\n\n"
                        f"Нажми��е кнопку ниже, чтобы подтвердить приглашение:",
                        view=ConfirmJoinView()
                    )
                    await sel_interaction.response.send_message(f"✅ Приглашение отправлено {member.mention}!",
                                                                ephemeral=True)
                    await asyncio.sleep(10)
                    await interaction.channel.set_permissions(member, overwrite=None)
                except:
                    await sel_interaction.response.send_message(f"❌ Не удалось отправить приглашение {member.mention}!",
                                                                ephemeral=True)

        view = View()
        view.add_item(InviteSelect())
        await interaction.response.send_message(view=view, ephemeral=True)


class TicketManagementView(View):
    def __init__(self, user, category, original_message_id, ticket_channel_id):
        super().__init__(timeout=None)
        self.add_item(CloseTicketButton(ticket_channel_id))
        self.add_item(InviteTicketButton(user, category))


# ==================== МОДЕРАЦИЯ (МУТ И БАН) ====================

async def _auto_unmute(user_id: int, guild_id: int, mute_role_id: int, delay_seconds: int, moderator):
    """Фоновая задача: снять мут через delay_seconds секунд"""
    await asyncio.sleep(delay_seconds)
    if user_id not in active_mutes:
        return  # Уже снят вручную

    del active_mutes[user_id]

    guild = bot.get_guild(guild_id)
    if not guild:
        return

    mute_role = guild.get_role(mute_role_id)
    member = guild.get_member(user_id)
    if member and mute_role:
        try:
            await member.remove_roles(mute_role, reason="Мут истёк")
        except Exception:
            pass
    # Нативный тайм-аут Discord истекает сам — снимать не нужно

    # Обновляем историю
    for p in punishment_history:
        if p["user_id"] == user_id and p["type"] == "мут" and p["status"] == "активный":
            p["status"] = "истёк"

    # Лог
    archive_channel = bot.get_channel(ARCHIVE_CHANNEL_ID)
    if archive_channel and member:
        embed = discord.Embed(
            title="🔊 Мут истёк (авто)",
            description=f"**Пользователь:** {member.mention} ({member.id})\n**Выдавал:** {moderator.mention}",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await archive_channel.send(embed=embed)


async def _auto_unban(user_id: int, guild_id: int, ban_role_id: int, delay_seconds: int, moderator):
    """Фоновая задача: снять бан через delay_seconds секунд"""
    await asyncio.sleep(delay_seconds)
    if user_id not in active_bans:
        return  # Уже снят вручную

    del active_bans[user_id]

    guild = bot.get_guild(guild_id)
    if not guild:
        return

    ban_role = guild.get_role(ban_role_id)
    member = guild.get_member(user_id)

    # Убираем роль с участника
    if member and ban_role:
        try:
            await member.remove_roles(ban_role, reason="Бан истёк")
        except Exception:
            pass

    # Снимаем overwrite роли бана с каналов ТОЛЬКО если больше нет активных банов
    if ban_role and not active_bans:
        for ch in guild.channels:
            try:
                await ch.set_permissions(ban_role, overwrite=None)
            except Exception:
                pass

    # Обновляем историю
    for p in punishment_history:
        if p["user_id"] == user_id and p["type"] == "бан" and p["status"] == "активный":
            p["status"] = "истёк"

    # Лог
    archive_channel = bot.get_channel(ARCHIVE_CHANNEL_ID)
    if archive_channel:
        user_mention = member.mention if member else f"<@{user_id}>"
        avatar_url = member.display_avatar.url if member else None
        embed = discord.Embed(
            title="✅ Бан истёк (авто)",
            description=f"**Пользователь:** {user_mention} ({user_id})\n**Выдавал:** {moderator.mention}",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        await archive_channel.send(embed=embed)


class ModerationModal(Modal):
    def __init__(self, action_type: str):
        super().__init__(title=f"Применить {action_type}")
        self.action_type = action_type

        self.add_item(TextInput(
            label="ID или упоминание пользователя",
            placeholder="Введите ID пользователя или @упоминание",
            required=True,
            custom_id="user_id"
        ))

        self.add_item(TextInput(
            label="Длительность (в минутах)",
            placeholder="Введите количество минут",
            required=True,
            custom_id="duration"
        ))

        self.add_item(TextInput(
            label="Причина",
            placeholder="Укажите причину",
            required=True,
            style=discord.TextStyle.paragraph,
            custom_id="reason"
        ))

    async def on_submit(self, interaction: discord.Interaction):
        # СРАЗУ откладываем ответ — защита от 404 Unknown interaction
        await interaction.response.defer(ephemeral=True)

        user_input = self.children[0].value.strip()
        try:
            duration_minutes = int(self.children[1].value)
        except ValueError:
            await interaction.followup.send("❌ Длительность должна быть числом!", ephemeral=True)
            return

        if duration_minutes <= 0:
            await interaction.followup.send("❌ Длительность должна быть больше 0!", ephemeral=True)
            return

        reason = self.children[2].value
        guild = interaction.guild

        # Пытаемся получить пользователя
        user = None
        try:
            if user_input.startswith("<@") and user_input.endswith(">"):
                uid_str = user_input[2:-1].lstrip("!")
                user_id = int(uid_str)
            else:
                user_id = int(user_input)
            user = guild.get_member(user_id) or await bot.fetch_user(user_id)
        except (ValueError, discord.NotFound):
            pass

        if not user:
            await interaction.followup.send("❌ Пользователь не найден!", ephemeral=True)
            return

        member = guild.get_member(user.id)

        try:
            if self.action_type == "мут":
                if not member:
                    await interaction.followup.send("❌ Пользователь не на сервере!", ephemeral=True)
                    return

                # Нативный тайм-аут Discord работает максимум 28 дней
                timeout_minutes = min(duration_minutes, 28 * 24 * 60)

                # ГЛАВНОЕ: нативный тайм-аут Discord — блокирует сообщения,
                # реакции и голос ВЕЗДЕ, независимо от ролей и прав каналов
                try:
                    await member.timeout(
                        timedelta(minutes=timeout_minutes),
                        reason=f"Мут: {reason}"
                    )
                except discord.Forbidden:
                    await interaction.followup.send(
                        "❌ У бота нет права **Модерация участников** (Moderate Members) "
                        "или роль бота ниже роли пользователя!",
                        ephemeral=True
                    )
                    return

                # Роль — визуальная метка мута
                mute_role = discord.utils.get(guild.roles, name=mute_role_name)
                if not mute_role:
                    mute_role = await guild.create_role(
                        name=mute_role_name,
                        color=discord.Color.orange(),
                        reason="Роль-метка для замутированных"
                    )
                try:
                    await member.add_roles(mute_role, reason=f"Мут: {reason}")
                except Exception:
                    pass

                # Добавляем мут в хранилище
                active_mutes[user.id] = {
                    "moderator": interaction.user,
                    "reason": reason,
                    "duration": duration_minutes,
                    "timestamp": datetime.now(),
                    "end_time": datetime.now() + timedelta(minutes=duration_minutes),
                    "guild_id": guild.id,
                    "mute_role_id": mute_role.id,
                }

                # Лог в архив
                archive_channel = bot.get_channel(ARCHIVE_CHANNEL_ID)
                if archive_channel:
                    embed = discord.Embed(
                        title="🔇 Пользователь замучен",
                        description=(
                            f"**Пользователь:** {user.mention} ({user.id})\n"
                            f"**Модератор:** {interaction.user.mention}\n"
                            f"**Причина:** {reason}\n"
                            f"**Длительность:** {duration_minutes} мин."
                        ),
                        color=discord.Color.orange(),
                        timestamp=discord.utils.utcnow()
                    )
                    embed.set_thumbnail(url=user.display_avatar.url)
                    await archive_channel.send(embed=embed)

                # Сохраняем в историю
                punishment_history.append({
                    "type": "мут", "user": user, "user_id": user.id,
                    "moderator": interaction.user, "reason": reason,
                    "duration": duration_minutes, "timestamp": datetime.now(), "status": "активный"
                })

                await interaction.followup.send(
                    f"🔇 {user.mention} замучен на **{duration_minutes} мин.**\n**Причина:** {reason}",
                    ephemeral=True
                )

                # Таймер снятия метки-роли (сам тайм-аут Discord снимет автоматически)
                asyncio.create_task(
                    _auto_unmute(user.id, guild.id, mute_role.id, duration_minutes * 60, interaction.user)
                )

            elif self.action_type == "бан":
                # Получаем или создаём роль бана
                ban_role = discord.utils.get(guild.roles, name=ban_role_name)
                if not ban_role:
                    ban_role = await guild.create_role(
                        name=ban_role_name,
                        color=discord.Color.dark_red(),
                        reason="Роль для забаненных пользователей"
                    )

                # Настраиваем права роли бана на всех каналах (кроме бан-каналов)
                for ch in guild.channels:
                    if ch.id not in [BAN_CHANNEL_1, BAN_CHANNEL_2]:
                        try:
                            await ch.set_permissions(ban_role, view_channel=False)
                        except Exception:
                            pass

                # Даём доступ (только чтение) к каналам для забаненных
                for ban_ch_id in [BAN_CHANNEL_1, BAN_CHANNEL_2]:
                    ban_ch = guild.get_channel(ban_ch_id)
                    if ban_ch:
                        try:
                            await ban_ch.set_permissions(ban_role,
                                view_channel=True, read_message_history=True, send_messages=False)
                        except Exception:
                            pass

                if member:
                    await member.add_roles(ban_role, reason=f"Бан: {reason}")
                    # Отключаем из войса
                    if member.voice:
                        try:
                            await member.move_to(None)
                        except Exception:
                            pass

                # Добавляем бан
                active_bans[user.id] = {
                    "moderator": interaction.user,
                    "reason": reason,
                    "duration": duration_minutes,
                    "timestamp": datetime.now(),
                    "end_time": datetime.now() + timedelta(minutes=duration_minutes),
                    "guild_id": guild.id,
                    "ban_role_id": ban_role.id,
                    "permanent": False
                }

                # Лог в архив
                archive_channel = bot.get_channel(ARCHIVE_CHANNEL_ID)
                if archive_channel:
                    embed = discord.Embed(
                        title="🚫 Пользователь забанен",
                        description=(
                            f"**Пользователь:** {user.mention} ({user.id})\n"
                            f"**Модератор:** {interaction.user.mention}\n"
                            f"**Причина:** {reason}\n"
                            f"**Длительность:** {duration_minutes} мин."
                        ),
                        color=discord.Color.red(),
                        timestamp=discord.utils.utcnow()
                    )
                    embed.set_thumbnail(url=user.display_avatar.url)
                    await archive_channel.send(embed=embed)

                # Сохраняем в историю
                punishment_history.append({
                    "type": "бан", "user": user, "user_id": user.id,
                    "moderator": interaction.user, "reason": reason,
                    "duration": duration_minutes, "timestamp": datetime.now(), "status": "активный"
                })

                # Обновляем ephemeral-сообщение через followup
                try:
                    await interaction.followup.send(
                        f"🚫 {user.mention} забанен на **{duration_minutes} мин.**\n**Причина:** {reason}",
                        ephemeral=True
                    )
                except Exception:
                    pass

                # Запускаем таймер автоснятия бана в фоне
                asyncio.create_task(
                    _auto_unban(user.id, guild.id, ban_role.id, duration_minutes * 60, interaction.user)
                )

        except Exception as e:
            try:
                await interaction.followup.send(f"❌ Ошибка: {str(e)}", ephemeral=True)
            except Exception:
                pass
            print(f"Ошибка при применении {self.action_type}: {e}")


class RemovalModal(Modal):
    def __init__(self, removal_type: str):
        super().__init__(title=f"Снять {removal_type}")
        self.removal_type = removal_type

        self.add_item(TextInput(
            label="ID пользователя",
            placeholder="Введите ID пользователя",
            required=True,
            custom_id="user_id"
        ))

        self.add_item(TextInput(
            label="Причина снятия",
            placeholder="Укажите причину",
            required=False,
            style=discord.TextStyle.paragraph,
            custom_id="reason"
        ))

    async def on_submit(self, interaction: discord.Interaction):
        # СРАЗУ откладываем ответ — защита от 404 Unknown interaction
        await interaction.response.defer(ephemeral=True)

        try:
            user_id_str = self.children[0].value.strip()
            reason = self.children[1].value or "Не указана"

            # Поддержка упоминания и голого ID
            if user_id_str.startswith("<@") and user_id_str.endswith(">"):
                user_id = int(user_id_str[2:-1].lstrip("!"))
            else:
                user_id = int(user_id_str)

            try:
                user = await bot.fetch_user(user_id)
            except discord.NotFound:
                await interaction.followup.send("❌ Пользователь не найден!", ephemeral=True)
                return

            guild = interaction.guild
            archive_channel = bot.get_channel(ARCHIVE_CHANNEL_ID)

            if self.removal_type == "мут":
                if user_id not in active_mutes:
                    await interaction.followup.send("❌ У пользователя нет активного мута!", ephemeral=True)
                    return

                del active_mutes[user_id]

                member = guild.get_member(user.id)
                if member:
                    # Снимаем нативный тайм-аут Discord
                    try:
                        await member.timeout(None, reason="Мут снят модератором")
                    except Exception:
                        pass
                    # Снимаем роль-метку
                    mute_role = discord.utils.get(guild.roles, name=mute_role_name)
                    if mute_role:
                        try:
                            await member.remove_roles(mute_role, reason="Мут снят модератором")
                        except Exception:
                            pass

                # Обновляем статус в истории
                for p in punishment_history:
                    if p["user_id"] == user_id and p["type"] == "мут" and p["status"] == "активный":
                        p["status"] = "снят вручную"

                if archive_channel:
                    embed = discord.Embed(
                        title="🔊 Мут снят",
                        description=(
                            f"**Пользователь:** {user.mention} ({user.id})\n"
                            f"**Модератор:** {interaction.user.mention}\n"
                            f"**Причина:** {reason}"
                        ),
                        color=discord.Color.green(),
                        timestamp=discord.utils.utcnow()
                    )
                    embed.set_thumbnail(url=user.display_avatar.url)
                    await archive_channel.send(embed=embed)

                await interaction.followup.send(f"✅ Мут снят для {user.mention}", ephemeral=True)

            elif self.removal_type == "бан":
                if user_id not in active_bans:
                    await interaction.followup.send("❌ У пользователя нет активного бана!", ephemeral=True)
                    return

                del active_bans[user_id]

                # Удаляем роль с участника
                ban_role = discord.utils.get(guild.roles, name=ban_role_name)
                member = guild.get_member(user.id)
                if member and ban_role:
                    try:
                        await member.remove_roles(ban_role, reason="Бан снят модератором")
                    except Exception:
                        pass

                # Чистим overwrite роли бана с каналов ТОЛЬКО если больше нет активных банов
                if ban_role and not active_bans:
                    for ch in guild.channels:
                        try:
                            await ch.set_permissions(ban_role, overwrite=None)
                        except Exception:
                            pass

                # Обновляем статус в истории
                for p in punishment_history:
                    if p["user_id"] == user_id and p["type"] == "бан" and p["status"] == "активный":
                        p["status"] = "снят вручную"

                if archive_channel:
                    embed = discord.Embed(
                        title="✅ Бан снят",
                        description=(
                            f"**Пользователь:** {user.mention} ({user.id})\n"
                            f"**Модератор:** {interaction.user.mention}\n"
                            f"**Причина:** {reason}"
                        ),
                        color=discord.Color.green(),
                        timestamp=discord.utils.utcnow()
                    )
                    embed.set_thumbnail(url=user.display_avatar.url)
                    await archive_channel.send(embed=embed)

                await interaction.followup.send(f"✅ Бан снят для {user.mention}", ephemeral=True)

        except ValueError:
            await interaction.followup.send("❌ Некорректный ID пользователя!", ephemeral=True)
        except Exception as e:
            print(f"Ошибка RemovalModal: {e}")
            try:
                await interaction.followup.send(f"❌ Ошибка: {str(e)}", ephemeral=True)
            except Exception:
                pass


# ==================== ВЕЧНЫЕ ПАНЕЛИ (PERSISTENT VIEWS) ====================

def _has_mod_rights(interaction: discord.Interaction, ban_level=False) -> bool:
    """Проверка прав для кнопок панели модерации"""
    roles = interaction.user.roles
    is_admin = discord.utils.get(interaction.guild.roles, name="Админ") in roles
    is_curator = discord.utils.get(interaction.guild.roles, name="Куратор") in roles
    is_moder = discord.utils.get(interaction.guild.roles, name="Модератор") in roles
    if ban_level:
        return is_admin or is_curator
    return is_admin or is_curator or is_moder


class ModerationPanelView(View):
    """Вечная панель модерации — работает после перезапуска бота"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔇 Мут", style=discord.ButtonStyle.danger, custom_id="modpanel:mute")
    async def mute_button(self, interaction: discord.Interaction, button: Button):
        if not _has_mod_rights(interaction):
            await interaction.response.send_message("❌ У вас нет прав!", ephemeral=True)
            return
        await interaction.response.send_modal(ModerationModal("мут"))

    @discord.ui.button(label="🚫 Бан", style=discord.ButtonStyle.danger, custom_id="modpanel:ban")
    async def ban_button(self, interaction: discord.Interaction, button: Button):
        if not _has_mod_rights(interaction, ban_level=True):
            await interaction.response.send_message("❌ Бан могут выдавать только Админ и Куратор!", ephemeral=True)
            return
        await interaction.response.send_modal(ModerationModal("бан"))

    @discord.ui.button(label="🔊 Снять мут", style=discord.ButtonStyle.success, custom_id="modpanel:unmute")
    async def unmute_button(self, interaction: discord.Interaction, button: Button):
        if not _has_mod_rights(interaction):
            await interaction.response.send_message("❌ У вас нет прав!", ephemeral=True)
            return
        await interaction.response.send_modal(RemovalModal("мут"))

    @discord.ui.button(label="✅ Снять бан", style=discord.ButtonStyle.success, custom_id="modpanel:unban")
    async def unban_button(self, interaction: discord.Interaction, button: Button):
        if not _has_mod_rights(interaction, ban_level=True):
            await interaction.response.send_message("❌ Снимать бан могут только Админ и Куратор!", ephemeral=True)
            return
        await interaction.response.send_modal(RemovalModal("бан"))

    @discord.ui.button(label="📋 История", style=discord.ButtonStyle.blurple, custom_id="modpanel:history")
    async def history_button(self, interaction: discord.Interaction, button: Button):
        if not _has_mod_rights(interaction):
            await interaction.response.send_message("❌ У вас нет прав!", ephemeral=True)
            return
        if not punishment_history:
            await interaction.response.send_message("❌ История наказаний пуста!", ephemeral=True)
            return

        history_text = "**📋 ИСТОРИЯ НАКАЗАНИЙ:**\n\n"
        for i, p in enumerate(punishment_history[-10:], 1):
            status_emoji = "🔴" if p["status"] == "активный" else "✅" if p["status"] == "истёк" else "⏹️"
            history_text += f"{i}. {status_emoji} **{p['type'].upper()}** - {p['user'].mention}\n"
            history_text += f"   Модератор: {p['moderator'].mention}\n"
            history_text += f"   Причина: {p['reason']}\n"
            history_text += f"   Длительность: {p['duration']}\n"
            history_text += f"   Статус: {p['status']}\n\n"

        await interaction.response.send_message(history_text, ephemeral=True)


class SupportPanelSelect(Select):
    """Вечное меню выбора категории тикета"""

    def __init__(self):
        super().__init__(
            placeholder="Выберите категорию...",
            options=[
                discord.SelectOption(label="Пожалаться на участника", description="Сообщить о нарушении"),
                discord.SelectOption(label="Заявка на модератора", description="Стать участником команды"),
                discord.SelectOption(label="Обжаловать блокировку", description="Оспорить ограничения"),
                discord.SelectOption(label="Другое", description="Другой вопрос"),
            ],
            custom_id="supportpanel:category",
        )

    async def callback(self, interaction: discord.Interaction):
        category = self.values[0]
        await interaction.response.send_modal(TicketModal(category))


class SupportPanelView(View):
    """Вечная панель поддержки — работает после перезапуска бота"""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(SupportPanelSelect())


# ==================== КОМАНДЫ ====================

@bot.command()
async def unmute(ctx):
    """Снять мут"""
    has_admin_role = discord.utils.get(ctx.guild.roles, name="Админ") in ctx.author.roles
    has_curator_role = discord.utils.get(ctx.guild.roles, name="Куратор") in ctx.author.roles
    has_moderator_role = discord.utils.get(ctx.guild.roles, name="Модератор") in ctx.author.roles

    if not has_admin_role and not has_curator_role and not has_moderator_role:
        await ctx.send("❌ У вас нет прав!", delete_after=5)
        return

    await ctx.send("⚙️ Используйте кнопку **🔊 Снять мут** в панели модерации: `!moderation`", delete_after=10)


@bot.command()
async def unban(ctx):
    """Снять бан"""
    has_admin_role = discord.utils.get(ctx.guild.roles, name="Админ") in ctx.author.roles
    has_curator_role = discord.utils.get(ctx.guild.roles, name="Куратор") in ctx.author.roles

    if not has_admin_role and not has_curator_role:
        await ctx.send("❌ У вас нет прав!", delete_after=5)
        return

    await ctx.send("⚙️ Используйте кнопку **✅ Снять бан** в панели модерации: `!moderation`", delete_after=10)


@bot.command()
async def mute(ctx):
    """Команда для выдачи мута"""
    has_admin_role = discord.utils.get(ctx.guild.roles, name="Админ") in ctx.author.roles
    has_curator_role = discord.utils.get(ctx.guild.roles, name="Куратор") in ctx.author.roles
    has_moderator_role = discord.utils.get(ctx.guild.roles, name="Модератор") in ctx.author.roles

    if not has_admin_role and not has_curator_role and not has_moderator_role:
        await ctx.send("❌ У вас нет прав для выдачи мута!", delete_after=5)
        return

    await ctx.send("⚙️ Используйте кнопку **🔇 Мут** в панели модерации: `!moderation`", delete_after=10)


@bot.command()
async def ban(ctx):
    """Команда для выдачи бана"""
    has_admin_role = discord.utils.get(ctx.guild.roles, name="Админ") in ctx.author.roles
    has_curator_role = discord.utils.get(ctx.guild.roles, name="Куратор") in ctx.author.roles

    if not has_admin_role and not has_curator_role:
        await ctx.send("❌ У вас нет прав для выдачи бана!", delete_after=5)
        return

    await ctx.send("⚙️ Используйте кнопку **🚫 Бан** в панели модерации: `!moderation`", delete_after=10)


@bot.command()
async def moderation(ctx):
    """Панель модерации"""
    has_admin_role = discord.utils.get(ctx.guild.roles, name="Админ") in ctx.author.roles
    has_curator_role = discord.utils.get(ctx.guild.roles, name="Куратор") in ctx.author.roles
    has_moderator_role = discord.utils.get(ctx.guild.roles, name="Модератор") in ctx.author.roles

    if not has_admin_role and not has_curator_role and not has_moderator_role:
        await ctx.send("❌ У вас нет прав!", delete_after=5)
        return

    embed = discord.Embed(
        title="⚙️ Панель модерации",
        description="Выберите действие ниже:",
        color=discord.Color.greyple()
    )
    embed.add_field(name="🔇 Мут", value="Выдать мут пользователю (каналы видны, нет письма и голоса)", inline=False)
    embed.add_field(name="🚫 Бан", value="Забанить пользователя на время", inline=False)
    embed.add_field(name="🔊 Снять мут", value="Снять мут с пользователя", inline=False)
    embed.add_field(name="✅ Снять бан", value="Разбанить пользователя", inline=False)
    embed.add_field(name="📋 История", value="Посмотреть историю наказаний", inline=False)

    await ctx.send(embed=embed, view=ModerationPanelView())


@bot.command()
async def support(ctx):
    embed = discord.Embed(
        title="❓ Поддержка и обращения",
        description="Выберите категорию вашего обращения ниже и заполните форму.",
        color=0xFFA500
    )
    embed.set_thumbnail(url="https://i.ibb.co/DDjrkN9B/channels4-profile.jpg")

    await ctx.send(embed=embed, view=SupportPanelView())


@bot.command()
async def panel(ctx):
    embed = discord.Embed(
        title="❓ Поддержка и обращения",
        description="Выберите категорию вашего обращения ниже.",
        color=0xFFA500
    )
    embed.set_thumbnail(url="https://i.ibb.co/DDjrkN9B/channels4-profile.jpg")

    await ctx.send(embed=embed, view=SupportPanelView())


# ==================== ЗАПУСК БОТА ====================

bot.run(TOKEN)
