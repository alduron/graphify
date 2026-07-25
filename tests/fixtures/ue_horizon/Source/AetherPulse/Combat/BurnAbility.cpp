// Copyright AetherPulse. All Rights Reserved.
#include "BurnAbility.h"

void UBurnAbility::Activate()
{
    // Cross-boundary tag lookup: this string is the join point Blueprints share.
    FGameplayTag Tag = FGameplayTag::RequestGameplayTag(FName("Ability.Burn"));

    // Reflection-resolved handler bound by literal name (an event join point).
    OnBurnApplied.AddUFunction(this, FName("HandleBurnApplied"));

    // Input action bound by literal name.
    InputComponent->BindAction("FireWeapon", IE_Pressed, this, &UBurnAbility::Fire);

    // A second reference to the shared config key (drives cross-file promotion).
    const TCHAR* Key = TEXT("Game.Config.MaxPlayers");

    // NEGATIVE cases: none of the following must be indexed.
    // - A comment that mentions a fake tag Not.A.Real.Tag should be ignored.
    FString Message = "Burning the target right now";       // has spaces
    FString AssetPath = "Content/Abilities/GA_Burn.uasset";  // path, has slashes
    FString Version = "1.2.3";                               // numeric, not tag-shaped
    FString Lower = "system.config.value";                  // lowercase segments
}
