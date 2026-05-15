*** Settings ***
Documentation     Page object resource for Okey Turkano
Library           SeleniumLibrary

*** Variables ***
${URL}          https://okeyturkano.com/
${BROWSER}        Chrome

*** Keywords ***
Open Okey Turkano Page
    Open Browser    ${URL}    ${BROWSER}
    Maximize Browser Window

Close Test Browser
    Close All Browsers

🇹🇷 Tr Button Should Be Visible
    Element Should Be Visible    xpath=//button[contains(.,'🇹🇷 TR')]

🇬🇧 En Button Should Be Visible
    Element Should Be Visible    xpath=//button[contains(.,'🇬🇧 EN')]

🇵🇱 Pl Button Should Be Visible
    Element Should Be Visible    xpath=//button[contains(.,'🇵🇱 PL')]

Giriş Yap Button Should Be Visible
    Element Should Be Visible    id=btn-login-submit
