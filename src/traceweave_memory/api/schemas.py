from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class TextPart(Contract):
    type: Literal["text"]
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class ImageURL(Contract):
    url: str = Field(min_length=1)


class ImagePart(Contract):
    type: Literal["image_url"]
    image_url: ImageURL


Part = Annotated[TextPart | ImagePart, Field(discriminator="type")]
Content = str | Annotated[list[Part], Field(min_length=1)]


def valid_content(value):
    if isinstance(value, str) and not value.strip():
        raise ValueError("content must not be blank")
    return value


class Message(Contract):
    role: Literal["user", "assistant"]
    content: Content
    timestamp: int | None = None

    _content = field_validator("content")(valid_content)


class Scoped(Contract):
    user_id: str = Field(min_length=1)

    @field_validator("user_id")
    @classmethod
    def scope_nonblank(cls, value):
        if not value.strip():
            raise ValueError("user_id must not be blank")
        return value


class AddRequest(Scoped):
    request_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    messages: list[Message] = Field(min_length=1)

    @field_validator("request_id", "session_id")
    @classmethod
    def identifier_nonblank(cls, value):
        if not value.strip():
            raise ValueError("identifier must not be blank")
        return value


class AddResponse(Contract):
    success: Literal[True] = True
    request_id: str
    user_id: str
    session_id: str


class SearchRequest(Scoped):
    query: Content
    options: list[str] | None = None
    top_k: int = Field(ge=1, le=100)

    _query = field_validator("query")(valid_content)


class MemoryResult(Contract):
    id: str
    content: Content
    score: float
    created_at: str


class SearchResponse(Contract):
    data: list[MemoryResult]
