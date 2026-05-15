*** Settings ***
Documentation     AI smoke for Okey Turkano
Resource          ../resources/home.resource
Test Setup        Open Okey Turkano Page
Test Teardown     Close Test Browser

*** Test Cases ***
Okey Turkano Loads With Expected Title
    Title Should Be    Okey Turkano

🇹🇷 Tr Button Visibility
    🇹🇷 Tr Button Should Be Visible

🇬🇧 En Button Visibility
    🇬🇧 En Button Should Be Visible

🇵🇱 Pl Button Visibility
    🇵🇱 Pl Button Should Be Visible

Giriş Yap Button Visibility
    Giriş Yap Button Should Be Visible
