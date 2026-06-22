void id77(int array[256]){
  unsigned int i = 0;
  while (i < (sizeof(*array) / sizeof(array[0]))) {
    array[i] = i;
    i++;
  }
}