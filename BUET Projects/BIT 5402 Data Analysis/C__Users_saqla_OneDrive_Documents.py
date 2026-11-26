#!/usr/bin/env python
# coding: utf-8

# In[12]:


import pandas as pd

customers = pd.DataFrame({
    "name" : ["Anika", "Babul", "Chadni", "Salain", "Asif", "Mamun", "Nabiul", "Shohag", "Zamdani"],
    "city" : ["Dhaka", "Ctg", "Dhaka", "Dhaka", "Zhadpur", "Rangpur", "Khulna", "Dhaka", "Dhaka"],
    "age" : [28, 35, 22, 25, 42, 62, 72, 82, 42],
    "spent" : [15500, 200, 3100, 314500, 31400, 31300, 31020, 31100, 310900]
    })


# In[13]:


#show the first row of the table
customers.head()


# In[14]:


# Show the Shape of the Table
customers.shape


# In[15]:


customers.dtypes


# In[16]:


customers['spent']


# In[17]:


customers[["name", "spent"]]


# In[18]:


customers[customers["city"] == "Dhaka"]
customers[customers["spent"] > 10000]


# In[19]:


customers.query("city == 'Dhaka' and spent > 10000")


# In[20]:


customers.sort_values("spent", ascending=False)


# In[21]:


customers.nlargest(3, "spent")


# In[22]:


customers.min()


# In[23]:


customers["spent"].mean()


# In[24]:


customers.max()


# In[ ]:





# In[ ]:




