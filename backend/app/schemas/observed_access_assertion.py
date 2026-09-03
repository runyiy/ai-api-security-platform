from pydantic import BaseModel, ConfigDict, model_validator
from pydantic_core import PydanticCustomError


class ObservedAccessAssertionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def reject_fields(cls, value):
        if isinstance(value, dict) and value:
            raise PydanticCustomError(
                "resource_access_assertion_prohibited_field",
                "Resource access assertion contains a prohibited field.",
            )
        return value
