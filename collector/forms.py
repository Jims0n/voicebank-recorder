from django import forms
from .models import Speaker


class ConsentForm(forms.Form):
    understood = forms.BooleanField(label="I have read the information above and agree to take part.")
    adult = forms.BooleanField(label="I am 18 or older.")


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Speaker
        fields = ["age_band", "gender", "first_language", "first_language_other",
                  "state", "language_pref", "environment"]
        labels = {
            "age_band": "Age",
            "first_language": "First language",
            "first_language_other": "If other, which?",
            "state": "State you grew up in",
            "language_pref": "What do you speak day to day?",
            "environment": "Where are you recording right now?",
        }
        widgets = {f: forms.RadioSelect for f in
                   ["age_band", "gender", "first_language", "language_pref", "environment"]}


class ResumeForm(forms.Form):
    code = forms.CharField(max_length=8, label="Your speaker code")

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()
